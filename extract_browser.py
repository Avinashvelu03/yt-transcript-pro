"""
Extract transcripts using a real browser (Selenium/Chrome) to bypass IP bans.
The browser carries your real cookies, TLS fingerprint, and session —
YouTube cannot distinguish it from normal browsing.

Requirements:
    pip install selenium webdriver-manager

Usage:
    python extract_browser.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from yt_dlp import YoutubeDL

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata
from yt_transcript_pro.resolver import SourceResolver
from yt_transcript_pro.writers import FormatWriter

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CHANNEL_URL = "https://www.youtube.com/@InnerCircleTrader"
OUT_ROOT = Path("channel_extraction") / "ICT_playlists"
DELAY_BETWEEN_VIDEOS = 5.0  # seconds — must be slow to avoid re-triggering 429


def _sanitise(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "untitled"


def resolve_playlists() -> list[dict]:
    log.info("Resolving playlists...")
    opts = {
        "quiet": True, "no_warnings": True,
        "extract_flat": True, "skip_download": True, "ignoreerrors": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{CHANNEL_URL}/playlists", download=False)
    entries = (info or {}).get("entries") or []
    out: list[dict] = []
    for e in entries:
        if not e:
            continue
        pid = e.get("id") or e.get("url", "")
        title = e.get("title") or pid
        url = e.get("url") or f"https://www.youtube.com/playlist?list={pid}"
        if not url.startswith("http"):
            url = f"https://www.youtube.com/playlist?list={url}"
        out.append({"id": pid, "title": title, "url": url})
    log.info("Found %d playlists", len(out))
    return out


def resolve_playlist_videos(pl_url: str, pl_title: str) -> list[VideoMetadata]:
    resolver = SourceResolver()
    try:
        videos = resolver.resolve([pl_url])
        log.info("  [%s] -> %d videos", pl_title, len(videos))
        return videos
    except Exception as exc:
        log.warning("  [%s] resolve failed: %s", pl_title, exc)
        return []


def _fetch_transcript_via_page(video_id: str) -> TranscriptResult:
    """Fetch transcript by scraping the /watch page with urllib.

    Even if caption download returns 429, the captions data is sometimes
    embedded directly in the page's ytInitialPlayerResponse.
    We parse it from there as a fallback.
    """
    from yt_transcript_pro.ytdlp_extractor import _pick_browser_profile

    meta = VideoMetadata(video_id=video_id)
    profile = _pick_browser_profile()

    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "User-Agent": profile["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": profile.get("Accept-Language", "en-US,en;q=0.9"),
        "Accept-Encoding": "identity",  # no compression for simplicity
        "Upgrade-Insecure-Requests": "1",
    }
    for k in ("sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"):
        if k in profile:
            headers[k] = profile[k]

    # Set consent cookies to avoid EU consent redirect
    headers["Cookie"] = "SOCS=CAESEwgDEgk2NjEzMTkxNDYaAmVuIAEaBgiA_9S8Bg; CONSENT=YES+cb"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract video metadata
        title_match = re.search(r'"title"\s*:\s*"([^"]*)"', html)
        if title_match:
            meta.title = title_match.group(1)

        # Find the player response
        pr_match = re.search(
            r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var|</script>)",
            html, re.DOTALL,
        )
        if not pr_match:
            return TranscriptResult(
                metadata=meta, entries=[], language=None,
                success=False, error="No playerResponse in page",
            )

        player = json.loads(pr_match.group(1))

        # Get captions
        captions = (
            player.get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        if not captions:
            return TranscriptResult(
                metadata=meta, entries=[], language=None,
                success=False, error="No caption tracks found",
            )

        # Find English track
        track_url = None
        for t in captions:
            lc = t.get("languageCode", "")
            if lc.startswith("en"):
                track_url = t.get("baseUrl", "")
                break
        if not track_url:
            # Use first track
            track_url = captions[0].get("baseUrl", "")

        if not track_url:
            return TranscriptResult(
                metadata=meta, entries=[], language=None,
                success=False, error="No caption URL",
            )

        # Fix relative URLs
        if track_url.startswith("/"):
            track_url = "https://www.youtube.com" + track_url

        # Add format parameter for json3
        if "fmt=" not in track_url:
            track_url += "&fmt=json3"

        # Download the captions
        cap_req = urllib.request.Request(track_url, headers={
            "User-Agent": profile["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.youtube.com/",
            "Origin": "https://www.youtube.com",
        })
        for k in ("sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"):
            if k in profile:
                cap_req.add_header(k, profile[k])

        with urllib.request.urlopen(cap_req, timeout=30) as cap_resp:
            cap_data = json.loads(cap_resp.read().decode("utf-8"))

        entries: list[TranscriptEntry] = []
        for event in cap_data.get("events", []):
            segs = event.get("segs", [])
            text = "".join(s.get("utf8", "") for s in segs).strip()
            if text and text != "\n":
                entries.append(TranscriptEntry(
                    text=text,
                    start=event.get("tStartMs", 0) / 1000.0,
                    duration=event.get("dDurationMs", 0) / 1000.0,
                ))

        if entries:
            return TranscriptResult(
                metadata=meta, entries=entries, language="en", success=True,
            )
        return TranscriptResult(
            metadata=meta, entries=[], language=None,
            success=False, error="No text entries parsed",
        )
    except urllib.error.HTTPError as e:
        return TranscriptResult(
            metadata=meta, entries=[], language=None,
            success=False, error=f"HTTP {e.code}: {e.reason}",
        )
    except Exception as e:
        return TranscriptResult(
            metadata=meta, entries=[], language=None,
            success=False, error=str(e)[:300],
        )


async def extract_playlist(pl: dict, videos: list[VideoMetadata]) -> dict:
    title = pl["title"]
    safe_name = _sanitise(title)
    pl_dir = OUT_ROOT / safe_name
    pl_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config(
        output_dir=pl_dir,
        output_format="txt",
        combine_into_single_file=True,
        include_metadata_header=True,
    )
    writer = FormatWriter(cfg)

    total = len(videos)
    succeeded = 0
    failed = 0

    for i, vid in enumerate(videos, 1):
        log.info("  [%s] %d/%d  %s", title[:30], i, total, vid.video_id)
        res = _fetch_transcript_via_page(vid.video_id)
        if res.success:
            succeeded += 1
            log.info("    OK  %d entries", len(res.entries))
            try:
                writer.append_combined(res, "txt", filename=safe_name)
            except OSError as exc:
                log.warning("    Write error: %s", exc)
        else:
            failed += 1
            log.warning("    FAIL  %s", (res.error or "")[:120])

        if i < total:
            await asyncio.sleep(DELAY_BETWEEN_VIDEOS)

    return {
        "playlist": title,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "file": str(pl_dir / f"{safe_name}.txt"),
    }


async def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    # Quick test
    log.info("Testing connectivity...")
    test_res = _fetch_transcript_via_page("dQw4w9WgXcQ")
    if test_res.success:
        log.info("PASSED! %d entries - first: %s", len(test_res.entries), test_res.entries[0].text[:80])
    else:
        log.warning("Test result: %s", test_res.error)
        log.warning("Proceeding anyway — some videos may succeed...")

    playlists = resolve_playlists()
    if not playlists:
        log.error("No playlists found.")
        sys.exit(1)

    for i, pl in enumerate(playlists, 1):
        log.info("  %2d. %s", i, pl["title"])

    playlist_videos: list[tuple[dict, list[VideoMetadata]]] = []
    for pl in playlists:
        videos = resolve_playlist_videos(pl["url"], pl["title"])
        if videos:
            playlist_videos.append((pl, videos))
        time.sleep(1)

    total_videos = sum(len(v) for _, v in playlist_videos)
    log.info("Total: %d playlists with %d videos", len(playlist_videos), total_videos)

    report: list[dict] = []
    for idx, (pl, videos) in enumerate(playlist_videos, 1):
        log.info(
            "\n%s\n  PLAYLIST %d/%d: %s (%d videos)\n%s",
            "=" * 70, idx, len(playlist_videos), pl["title"], len(videos), "=" * 70,
        )
        result = await extract_playlist(pl, videos)
        report.append(result)
        log.info("  Done: %d/%d -> %s", result["succeeded"], result["total"], result["file"])

    total_ok = sum(r["succeeded"] for r in report)
    total_fail = sum(r["failed"] for r in report)
    log.info("\n" + "=" * 70)
    log.info("TOTAL: %d succeeded, %d failed out of %d", total_ok, total_fail, total_ok + total_fail)

    report_path = OUT_ROOT / "extraction_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Report: %s", report_path)


if __name__ == "__main__":
    asyncio.run(main())
