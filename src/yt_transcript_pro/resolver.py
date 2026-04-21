"""Resolve user inputs (URLs, IDs) into concrete video lists.

Supports:
    * Single video URL / ID
    * Playlist URL
    * Channel URL (/c/, /@handle, /channel/UC..., /user/)
    * Text files containing any of the above (one per line)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from yt_transcript_pro.models import VideoMetadata

# 11-char YouTube ID (URL-safe alphabet: A-Z a-z 0-9 _ -)
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID_RE = re.compile(r"^(PL|UU|LL|FL|RD|OL)[A-Za-z0-9_-]{10,}$")


class SourceResolver:
    """Resolve input sources to a list of video metadata objects."""

    def __init__(self, ydl_opts: dict[str, object] | None = None) -> None:
        # Default yt-dlp options: quiet, flat (no per-video network trip)
        self.ydl_opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": True,
        }
        if ydl_opts:
            self.ydl_opts.update(ydl_opts)

    # ---------- public helpers ----------

    @staticmethod
    def extract_video_id(value: str) -> str | None:
        """Extract a video ID from a URL or raw ID string. Returns None if not found."""
        value = value.strip()
        if not value:
            return None
        if _VIDEO_ID_RE.match(value):
            return value
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        if parsed.hostname is None:
            return None
        host = parsed.hostname.lower()
        if host.startswith("www."):
            host = host[4:]
        if host == "youtu.be":
            candidate = parsed.path.lstrip("/")
            return candidate if _VIDEO_ID_RE.match(candidate) else None
        if "youtube.com" in host:
            qs = parse_qs(parsed.query)
            if "v" in qs and _VIDEO_ID_RE.match(qs["v"][0]):
                return qs["v"][0]
            # /shorts/<id> or /embed/<id> or /live/<id>
            for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
                if parsed.path.startswith(prefix):
                    candidate = parsed.path[len(prefix):].split("/")[0]
                    return candidate if _VIDEO_ID_RE.match(candidate) else None
        return None

    @staticmethod
    def classify(source: str) -> str:
        """Return one of: 'video', 'playlist', 'channel', 'file', 'unknown'."""
        source = source.strip()
        if not source:
            return "unknown"
        if Path(source).is_file():
            return "file"
        if _VIDEO_ID_RE.match(source):
            return "video"
        if _PLAYLIST_ID_RE.match(source):
            return "playlist"
        if SourceResolver.extract_video_id(source):
            return "video"
        low = source.lower()
        if "list=" in low or "/playlist" in low:
            return "playlist"
        if any(
            token in low
            for token in ("/channel/", "/c/", "/user/", "youtube.com/@", "/@")
        ):
            return "channel"
        return "unknown"

    # ---------- resolution ----------

    def resolve(self, sources: Iterable[str]) -> list[VideoMetadata]:
        """Resolve multiple inputs into a de-duplicated list of VideoMetadata."""
        seen: set[str] = set()
        results: list[VideoMetadata] = []
        for src in sources:
            for meta in self._resolve_single(src):
                if meta.video_id not in seen:
                    seen.add(meta.video_id)
                    results.append(meta)
        return results

    def _resolve_single(self, source: str) -> list[VideoMetadata]:
        kind = self.classify(source)
        if kind == "video":
            return self._resolve_video(source)
        if kind == "playlist":
            return self._resolve_with_ydl(source)
        if kind == "channel":
            return self._resolve_channel(source)
        if kind == "file":
            return self._resolve_file(source)
        return []

    def _resolve_video(self, source: str) -> list[VideoMetadata]:
        vid = self.extract_video_id(source) or source
        return [
            VideoMetadata(
                video_id=vid,
                url=f"https://www.youtube.com/watch?v={vid}",
            )
        ]

    def _resolve_file(self, path_str: str) -> list[VideoMetadata]:
        path = Path(path_str)
        out: list[VideoMetadata] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            out.extend(self._resolve_single(line))
        return out

    def _resolve_channel(self, url: str) -> list[VideoMetadata]:
        # yt-dlp will auto-expand /videos for a channel URL. Force it if absent.
        if "/videos" not in url and "list=" not in url:
            url = url.rstrip("/") + "/videos"
        return self._resolve_with_ydl(url)

    def _resolve_with_ydl(self, url: str) -> list[VideoMetadata]:
        # Imported here so tests can monkeypatch easily
        from yt_dlp import YoutubeDL  # pragma: no cover - thin wrapper

        with YoutubeDL(self.ydl_opts) as ydl:  # pragma: no cover
            info = ydl.extract_info(url, download=False)
        return self._flatten_entries(info or {})

    @staticmethod
    def _flatten_entries(info: dict[str, object]) -> list[VideoMetadata]:
        out: list[VideoMetadata] = []
        entries = info.get("entries") or []
        channel = str(info.get("channel") or info.get("uploader") or "")
        channel_id = str(info.get("channel_id") or info.get("uploader_id") or "")
        if not isinstance(entries, list):
            return out
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # Nested playlist (channel has tabs of playlists)
            if entry.get("_type") == "playlist" and entry.get("entries"):
                out.extend(SourceResolver._flatten_entries(entry))
                continue
            vid = entry.get("id")
            if not isinstance(vid, str) or not _VIDEO_ID_RE.match(vid):
                continue
            try:
                out.append(
                    VideoMetadata(
                        video_id=vid,
                        title=str(entry.get("title") or ""),
                        channel=str(entry.get("channel") or channel or ""),
                        channel_id=str(entry.get("channel_id") or channel_id or ""),
                        duration_seconds=(
                            int(entry["duration"])
                            if isinstance(entry.get("duration"), (int, float))
                            else None
                        ),
                        view_count=(
                            int(entry["view_count"])
                            if isinstance(entry.get("view_count"), (int, float))
                            else None
                        ),
                        url=str(
                            entry.get("url")
                            or f"https://www.youtube.com/watch?v={vid}"
                        ),
                    )
                )
            except ValueError:
                continue
        return out
