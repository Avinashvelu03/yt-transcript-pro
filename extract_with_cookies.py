"""
Extract transcripts using browser cookies to bypass YouTube IP rate limits.

This script exports cookies from your Chrome browser and uses them to
authenticate the subtitle/timedtext download requests.

Usage:
    python extract_with_cookies.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
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

# ---- Tuning: slow and steady to avoid re-triggering rate limits ----
CONCURRENCY = 1
MAX_RETRIES = 5
RETRY_DELAY = 5.0
RETRY_MAX = 60.0
DELAY_BETWEEN_VIDEOS = 3.0  # seconds between each video


def _sanitise(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "untitled"


def resolve_playlists() -> list[dict]:
    log.info("Resolving playlists from %s/playlists ...", CHANNEL_URL)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
        "cookiesfrombrowser": ("chrome",),
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{CHANNEL_URL}/playlists", download=False)
        if not info:
            log.error("Could not fetch playlists page")
            return []
    entries = info.get("entries") or []
    out: list[dict] = []
    for entry in entries:
        if not entry:
            continue
        pl_id = entry.get("id") or entry.get("url", "")
        pl_title = entry.get("title") or pl_id
        pl_url = entry.get("url") or f"https://www.youtube.com/playlist?list={pl_id}"
        if not pl_url.startswith("http"):
            pl_url = f"https://www.youtube.com/playlist?list={pl_url}"
        out.append({"id": pl_id, "title": pl_title, "url": pl_url})
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


def _ydl_download_transcript(video_id: str) -> TranscriptResult:
    """Download transcript for a single video using yt-dlp with browser cookies."""
    meta = VideoMetadata(video_id=video_id)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "json3",
            "outtmpl": str(tmp_path / "%(id)s"),
            "cookiesfrombrowser": ("chrome",),
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}",
                    download=False,
                )
                if info:
                    meta.title = info.get("title") or ""
                    meta.channel = info.get("channel") or ""
                    meta.duration_seconds = (
                        int(info["duration"])
                        if isinstance(info.get("duration"), (int, float))
                        else None
                    )
                # Now download subs
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception as exc:
            return TranscriptResult(
                metadata=meta, entries=[], language=None,
                success=False, error=str(exc)[:500],
            )

        # Find the subtitle file
        sub_file = tmp_path / f"{video_id}.en.json3"
        if not sub_file.exists():
            # Try auto-generated
            for path in tmp_path.iterdir():
                if path.name.endswith(".json3"):
                    sub_file = path
                    break
            else:
                return TranscriptResult(
                    metadata=meta, entries=[], language=None,
                    success=False, error="No subtitle file created",
                )

        try:
            data = json.loads(sub_file.read_text(encoding="utf-8"))
            entries: list[TranscriptEntry] = []
            for event in data.get("events", []):
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
                    metadata=meta, entries=entries, language="en",
                    success=True,
                )
            return TranscriptResult(
                metadata=meta, entries=[], language=None,
                success=False, error="No text in subtitle events",
            )
        except Exception as exc:
            return TranscriptResult(
                metadata=meta, entries=[], language=None,
                success=False, error=f"Parse error: {exc}",
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
        log.info("  [%s] %d/%d  %s ...", title[:30], i, total, vid.video_id)
        res = _ydl_download_transcript(vid.video_id)

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

        # Gentle throttle between videos
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

    # Step 1: Quick connectivity test
    log.info("Testing connectivity with browser cookies...")
    test_res = _ydl_download_transcript("dQw4w9WgXcQ")
    if test_res.success:
        log.info("Connectivity test PASSED! (%d entries)", len(test_res.entries))
    else:
        log.error("Connectivity test FAILED: %s", test_res.error)
        log.error("Make sure Chrome is closed and cookies are accessible.")
        log.error("If Chrome is open, close it and try again.")
        sys.exit(1)

    # Step 2: Resolve playlists
    playlists = resolve_playlists()
    if not playlists:
        log.error("No playlists found.")
        sys.exit(1)

    log.info("=" * 70)
    for i, pl in enumerate(playlists, 1):
        log.info("  %2d. %s", i, pl["title"])
    log.info("=" * 70)

    # Step 3: Resolve videos
    playlist_videos: list[tuple[dict, list[VideoMetadata]]] = []
    for pl in playlists:
        videos = resolve_playlist_videos(pl["url"], pl["title"])
        if videos:
            playlist_videos.append((pl, videos))
        time.sleep(1)

    total_videos = sum(len(v) for _, v in playlist_videos)
    log.info("Total: %d playlists with %d videos", len(playlist_videos), total_videos)

    # Step 4: Extract each playlist
    report: list[dict] = []
    for idx, (pl, videos) in enumerate(playlist_videos, 1):
        log.info(
            "\n%s\n  PLAYLIST %d/%d: %s (%d videos)\n%s",
            "=" * 70, idx, len(playlist_videos), pl["title"], len(videos), "=" * 70,
        )
        result = await extract_playlist(pl, videos)
        report.append(result)
        log.info(
            "  Done: %d/%d succeeded -> %s",
            result["succeeded"], result["total"], result["file"],
        )

    # Final report
    log.info("\n" + "=" * 70)
    log.info("EXTRACTION COMPLETE")
    log.info("=" * 70)
    total_ok = sum(r["succeeded"] for r in report)
    total_fail = sum(r["failed"] for r in report)
    for r in report:
        status = "OK" if r["failed"] == 0 else f"{r['failed']} failed"
        log.info("  [%s] %d/%d  %s", status, r["succeeded"], r["total"], r["playlist"][:50])
    log.info("-" * 70)
    log.info("  TOTAL: %d succeeded, %d failed", total_ok, total_fail)

    report_path = OUT_ROOT / "extraction_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("  Report: %s", report_path)


if __name__ == "__main__":
    asyncio.run(main())
