"""Extract and consolidate transcripts for every public channel playlist.

The script is intentionally resume-friendly:

* previously rendered transcript blocks are reused from seed files and
  ``channel_extraction/ICT_playlists/transcripts/*.txt``;
* each newly fetched transcript is written as its own cache file before the
  final consolidated file is rebuilt;
* failures are reported separately so a later run can retry transient blocks
  without duplicating transcript text.

Usage:
    python extract_playlists.py
    python extract_playlists.py --no-fetch
    python extract_playlists.py --cookies cookies.txt --retry-failures
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor
from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptResult, VideoMetadata
from yt_transcript_pro.resolver import SourceResolver
from yt_transcript_pro.writers import FormatWriter
from yt_transcript_pro.ytdlp_extractor import _classify_error

CHANNEL_URL = "https://www.youtube.com/@InnerCircleTrader"
OUT_ROOT = Path("channel_extraction") / "ICT_playlists"
COMBINED_NAME = "InnerCircleTrader_playlist_transcripts"
DEFAULT_SEED = Path("..") / "InnerCircleTrader_all_transcripts.txt"

TRANSCRIPT_ID_RE = re.compile(r"^# Video ID: ([A-Za-z0-9_-]{11})\s*$", re.MULTILINE)
WORDS_RE = re.compile(r"^# Words: ([0-9]+)\s*$", re.MULTILINE)
TITLE_RE = re.compile(r"^# Title: (.+?)\s*$", re.MULTILINE)
SEPARATOR_RE = re.compile(r"\r?\n\r?\n={80}\r?\n\r?\n")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaylistSummary:
    id: str
    title: str
    url: str


@dataclass(frozen=True)
class PlaylistItem:
    playlist: PlaylistSummary
    playlist_index: int
    video_index: int
    video_count: int
    video: VideoMetadata


def _force_utf8() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")


def _sanitise(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "untitled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise_block(block: str) -> str:
    return block.strip() + "\n"


def _block_video_id(block: str) -> str | None:
    match = TRANSCRIPT_ID_RE.search(block)
    return match.group(1) if match else None


def _block_words(block: str) -> int:
    match = WORDS_RE.search(block)
    return int(match.group(1)) if match else len(block.split())


def _block_title(block: str) -> str:
    match = TITLE_RE.search(block)
    return match.group(1).strip() if match else ""


def _read_transcript_blocks(path: Path) -> dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks: dict[str, str] = {}
    for raw_block in SEPARATOR_RE.split(text):
        block = _normalise_block(raw_block)
        video_id = _block_video_id(block)
        if video_id:
            blocks[video_id] = block
    return blocks


def load_transcript_cache(seed_paths: list[Path], transcript_dir: Path) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for seed in seed_paths:
        seed_blocks = _read_transcript_blocks(seed)
        blocks.update(seed_blocks)
        if seed_blocks:
            log.info("Loaded %d transcript blocks from %s", len(seed_blocks), seed)

    if transcript_dir.exists():
        for path in sorted(transcript_dir.glob("*.txt")):
            blocks.update(_read_transcript_blocks(path))
    if blocks:
        log.info("Transcript cache has %d unique videos", len(blocks))
    return blocks


def resolve_playlists(channel_url: str = CHANNEL_URL) -> list[dict[str, str]]:
    """Return a list of public playlist dictionaries for a channel."""
    channel_url = channel_url.rstrip("/")
    playlists_url = f"{channel_url}/playlists"
    log.info("Resolving playlists from %s ...", playlists_url)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(playlists_url, download=False)
    if not info:
        return []

    out: list[dict[str, str]] = []
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        playlist_id = str(entry.get("id") or entry.get("url") or "").strip()
        title = str(entry.get("title") or playlist_id).strip()
        url = str(entry.get("url") or "").strip()
        if not url.startswith("http"):
            url = f"https://www.youtube.com/playlist?list={playlist_id or url}"
        if playlist_id and title and url:
            out.append({"id": playlist_id, "title": title, "url": url})
    log.info("Found %d playlists", len(out))
    return out


def resolve_playlist_videos(pl_url: str, pl_title: str) -> list[VideoMetadata]:
    """Resolve all video IDs inside one playlist."""
    resolver = SourceResolver()
    try:
        videos = resolver.resolve([pl_url])
    except Exception as exc:
        log.warning("  [%s] resolve failed: %s", pl_title, exc)
        return []
    log.info("  [%s] -> %d videos", pl_title, len(videos))
    return videos


def build_playlist_items(
    playlists: list[dict[str, str]],
    *,
    resolve_delay: float = 0.75,
) -> list[PlaylistItem]:
    items: list[PlaylistItem] = []
    for playlist_index, raw in enumerate(playlists, 1):
        playlist = PlaylistSummary(raw["id"], raw["title"], raw["url"])
        videos = resolve_playlist_videos(playlist.url, playlist.title)
        for video_index, video in enumerate(videos, 1):
            items.append(
                PlaylistItem(
                    playlist=playlist,
                    playlist_index=playlist_index,
                    video_index=video_index,
                    video_count=len(videos),
                    video=video,
                )
            )
        time.sleep(resolve_delay)
    return items


def unique_videos(items: list[PlaylistItem]) -> list[VideoMetadata]:
    seen: set[str] = set()
    out: list[VideoMetadata] = []
    for item in items:
        video_id = item.video.video_id
        if video_id in seen:
            continue
        seen.add(video_id)
        out.append(item.video)
    return out


def load_failures(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    failures = payload.get("failures", payload)
    return failures if isinstance(failures, dict) else {}


def save_failures(path: Path, failures: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _now_iso(),
        "failure_count": len(failures),
        "failures": failures,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _failure_record(result: TranscriptResult) -> dict[str, Any]:
    error = result.error or "unknown error"
    return {
        "video_id": result.metadata.video_id,
        "title": result.metadata.title,
        "url": result.metadata.url or f"https://www.youtube.com/watch?v={result.metadata.video_id}",
        "error": error,
        "category": _classify_error(error),
        "failed_at": _now_iso(),
    }


async def fetch_missing(
    videos: list[VideoMetadata],
    *,
    args: argparse.Namespace,
    transcript_dir: Path,
    transcript_blocks: dict[str, str],
    failures: dict[str, dict[str, Any]],
    failures_path: Path,
) -> None:
    if not videos:
        return

    cfg = Config(
        output_dir=transcript_dir,
        output_format="txt",
        combine_into_single_file=False,
        include_metadata_header=True,
        include_timestamps=args.timestamps,
        concurrency=args.concurrency,
        max_retries=args.retries,
        retry_initial_delay=args.retry_delay,
        retry_max_delay=args.retry_max_delay,
        cookies_file=args.cookies,
        user_agent=args.user_agent,
        request_timeout=args.request_timeout,
    )
    writer = FormatWriter(cfg)
    backend_order = [part.strip() for part in args.backend_order.split(",") if part.strip()]
    extractor = AutoTranscriptExtractor(cfg, backend_order=backend_order)

    def on_progress(done: int, total: int, result: TranscriptResult) -> None:
        video_id = result.metadata.video_id
        if result.success:
            block = writer.render(result, "txt")
            transcript_blocks[video_id] = block
            (transcript_dir / f"{video_id}.txt").write_text(block, encoding="utf-8")
            failures.pop(video_id, None)
            log.info("  OK %d/%d %s (%d words)", done, total, video_id, result.word_count)
        else:
            failures[video_id] = _failure_record(result)
            log.warning(
                "  XX %d/%d %s  %s",
                done,
                total,
                video_id,
                (result.error or "")[:160].replace("\n", " "),
            )
        save_failures(failures_path, failures)

    await extractor.fetch_many(videos, progress=on_progress)


def write_index(path: Path, items: list[PlaylistItem], blocks: dict[str, str], failures: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                "playlist_index",
                "playlist_id",
                "playlist_title",
                "playlist_url",
                "video_index",
                "video_id",
                "video_title",
                "video_url",
                "status",
                "words",
                "error",
            ]
        )
        for item in items:
            video_id = item.video.video_id
            block = blocks.get(video_id)
            failure = failures.get(video_id, {})
            writer.writerow(
                [
                    item.playlist_index,
                    item.playlist.id,
                    item.playlist.title,
                    item.playlist.url,
                    item.video_index,
                    video_id,
                    item.video.title or _block_title(block or ""),
                    item.video.url or f"https://www.youtube.com/watch?v={video_id}",
                    "available" if block else "missing",
                    _block_words(block) if block else "",
                    failure.get("error", ""),
                ]
            )


def _missing_block(item: PlaylistItem, failure: dict[str, Any] | None) -> str:
    video = item.video
    video_id = video.video_id
    url = video.url or f"https://www.youtube.com/watch?v={video_id}"
    lines = [
        f"# Title: {video.title or '(unknown)'}",
        f"# Channel: {video.channel or '(unknown)'}",
        f"# Video ID: {video_id}",
        f"# URL: {url}",
        "# Transcript Status: missing",
    ]
    if failure:
        lines.append(f"# Error Category: {failure.get('category', 'unknown')}")
        lines.append(f"# Error: {str(failure.get('error', ''))[:500]}")
    lines.extend(
        [
            "",
            "[No transcript text is available in the local cache or this extraction run.]",
            "",
        ]
    )
    return "\n".join(lines)


def write_combined(
    path: Path,
    *,
    channel_url: str,
    playlists: list[dict[str, str]],
    items: list[PlaylistItem],
    blocks: dict[str, str],
    failures: dict[str, dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    unique_ids = {item.video.video_id for item in items}
    available_ids = unique_ids & set(blocks)
    missing_ids = unique_ids - set(blocks)

    parts: list[str] = [
        "\n".join(
            [
                "# InnerCircleTrader Playlist Transcript Consolidation",
                f"# Source Channel: {channel_url}",
                f"# Generated At: {_now_iso()}",
                f"# Public Playlists: {len(playlists)}",
                f"# Playlist Video Memberships: {len(items)}",
                f"# Unique Playlist Videos: {len(unique_ids)}",
                f"# Transcript Blocks Available: {len(available_ids)}",
                f"# Missing or Unavailable Transcript Blocks: {len(missing_ids)}",
                "",
                "# Playlist Index",
                *[
                    f"# {idx:02d}. {pl['title']} ({pl['id']})"
                    for idx, pl in enumerate(playlists, 1)
                ],
                "",
            ]
        )
    ]

    current_playlist: str | None = None
    for item in items:
        if item.playlist.id != current_playlist:
            current_playlist = item.playlist.id
            parts.append(
                "\n".join(
                    [
                        "#" * 80,
                        f"# Playlist {item.playlist_index}/{len(playlists)}: {item.playlist.title}",
                        f"# Playlist ID: {item.playlist.id}",
                        f"# Playlist URL: {item.playlist.url}",
                        f"# Videos: {item.video_count}",
                        "#" * 80,
                        "",
                    ]
                )
            )

        video_id = item.video.video_id
        block = blocks.get(video_id) or _missing_block(item, failures.get(video_id))
        parts.append(
            "\n".join(
                [
                    "-" * 80,
                    f"# Playlist Item: {item.video_index}/{item.video_count}",
                    f"# Playlist: {item.playlist.title}",
                    "-" * 80,
                    "",
                    _normalise_block(block),
                ]
            )
        )

    path.write_text("\n".join(parts), encoding="utf-8")


def write_report(
    path: Path,
    *,
    channel_url: str,
    playlists: list[dict[str, str]],
    items: list[PlaylistItem],
    blocks: dict[str, str],
    failures: dict[str, dict[str, Any]],
    fetched_count: int,
) -> None:
    unique_ids = {item.video.video_id for item in items}
    available_ids = unique_ids & set(blocks)
    missing_ids = unique_ids - set(blocks)
    payload = {
        "generated_at": _now_iso(),
        "channel_url": channel_url,
        "playlist_count": len(playlists),
        "playlist_video_memberships": len(items),
        "unique_playlist_videos": len(unique_ids),
        "transcript_blocks_available": len(available_ids),
        "missing_or_unavailable": len(missing_ids),
        "attempted_fetches_this_run": fetched_count,
        "combined_file": str(path.with_name(f"{COMBINED_NAME}.txt")),
        "playlists": playlists,
        "missing_video_ids": sorted(missing_ids),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-url", default=CHANNEL_URL)
    parser.add_argument("--output-dir", type=Path, default=OUT_ROOT)
    parser.add_argument("--combined-name", default=COMBINED_NAME)
    parser.add_argument("--seed-transcripts", type=Path, action="append", default=[DEFAULT_SEED])
    parser.add_argument("--no-fetch", action="store_true", help="Only rebuild outputs from local cache.")
    parser.add_argument("--retry-failures", action="store_true", help="Retry permanent failures listed in failures.json.")
    parser.add_argument("--max-fetch", type=int, default=None, help="Limit missing videos fetched this run.")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument("--retry-max-delay", type=float, default=8.0)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--backend-order", default="watch,ytdlp,api")
    parser.add_argument("--cookies", type=Path, default=None)
    parser.add_argument("--user-agent", default=None)
    parser.add_argument("--timestamps", action="store_true")
    parser.add_argument("--resolve-delay", type=float, default=0.75)
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    out_root = args.output_dir
    transcript_dir = out_root / "transcripts"
    failures_path = out_root / "failures.json"
    report_path = out_root / "extraction_report.json"
    index_path = out_root / "playlist_video_index.tsv"
    combined_path = out_root / f"{args.combined_name}.txt"

    out_root.mkdir(parents=True, exist_ok=True)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    seed_paths = [path for path in args.seed_transcripts if path]
    blocks = load_transcript_cache(seed_paths, transcript_dir)
    failures = load_failures(failures_path)

    playlists = resolve_playlists(args.channel_url)
    if not playlists:
        log.error("No playlists found.")
        return 2
    items = build_playlist_items(playlists, resolve_delay=args.resolve_delay)
    videos = unique_videos(items)

    cached_ids = set(blocks)
    failed_ids = {
        video_id
        for video_id, failure in failures.items()
        if not args.retry_failures and failure.get("category") == "permanent"
    }
    missing = [video for video in videos if video.video_id not in cached_ids and video.video_id not in failed_ids]
    if args.max_fetch is not None:
        missing = missing[: args.max_fetch]

    log.info(
        "Resolved %d playlist memberships, %d unique videos, %d cached transcripts, %d fetch candidates",
        len(items),
        len(videos),
        len({video.video_id for video in videos} & cached_ids),
        len(missing),
    )

    fetched_count = 0
    if args.no_fetch:
        log.info("--no-fetch set; rebuilding consolidated outputs from local cache only.")
    elif missing:
        fetched_count = len(missing)
        await fetch_missing(
            missing,
            args=args,
            transcript_dir=transcript_dir,
            transcript_blocks=blocks,
            failures=failures,
            failures_path=failures_path,
        )
    save_failures(failures_path, failures)

    write_index(index_path, items, blocks, failures)
    write_combined(
        combined_path,
        channel_url=args.channel_url,
        playlists=playlists,
        items=items,
        blocks=blocks,
        failures=failures,
    )
    write_report(
        report_path,
        channel_url=args.channel_url,
        playlists=playlists,
        items=items,
        blocks=blocks,
        failures=failures,
        fetched_count=fetched_count,
    )
    log.info("Combined transcript file -> %s", combined_path)
    log.info("Playlist index -> %s", index_path)
    log.info("Report -> %s", report_path)
    log.info("Failures -> %s", failures_path)
    return 0


def main() -> int:
    _force_utf8()
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
