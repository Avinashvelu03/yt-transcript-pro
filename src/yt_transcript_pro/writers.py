"""Output writers for multiple transcript formats."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from pathlib import Path

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptResult

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(name: str, fallback: str) -> str:
    """Produce a filesystem-safe filename stem."""
    cleaned = _SAFE_FILENAME.sub("_", name).strip("._")
    return cleaned[:120] or fallback


def _format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    """Format seconds as SRT (HH:MM:SS,mmm) or VTT (HH:MM:SS.mmm)."""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = round((seconds - int(seconds)) * 1000)
    if ms == 1000:
        ms = 0
        s += 1
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


class FormatWriter:
    """Render TranscriptResult objects into various text formats."""

    def __init__(self, config: Config) -> None:
        self.config = config

    # ---------- rendering ----------

    def render(self, result: TranscriptResult, fmt: str) -> str:
        fmt = fmt.lower()
        if fmt == "txt":
            return self._render_txt(result)
        if fmt == "json":
            return self._render_json(result)
        if fmt == "srt":
            return self._render_srt(result)
        if fmt == "vtt":
            return self._render_vtt(result)
        if fmt == "md":
            return self._render_md(result)
        if fmt == "csv":
            return self._render_csv(result)
        raise ValueError(f"Unsupported format: {fmt}")

    def _header(self, result: TranscriptResult) -> str:
        if not self.config.include_metadata_header:
            return ""
        m = result.metadata
        lines = [
            f"# Title: {m.title or '(unknown)'}",
            f"# Channel: {m.channel or '(unknown)'}",
            f"# Video ID: {m.video_id}",
            f"# URL: {m.url}",
            f"# Language: {result.language or '(unknown)'}",
            f"# Auto-generated: {result.is_generated}",
            f"# Words: {result.word_count}",
            "",
        ]
        return "\n".join(lines)

    def _render_txt(self, result: TranscriptResult) -> str:
        header = self._header(result)
        if self.config.include_timestamps:
            body = "\n".join(
                f"[{_format_timestamp(e.start)}] {e.text}" for e in result.entries
            )
        else:
            body = result.plain_text
        return header + body + ("\n" if body and not body.endswith("\n") else "")

    @staticmethod
    def _render_json(result: TranscriptResult) -> str:
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)

    @staticmethod
    def _render_srt(result: TranscriptResult) -> str:
        buf = io.StringIO()
        for i, e in enumerate(result.entries, start=1):
            buf.write(f"{i}\n")
            buf.write(
                f"{_format_timestamp(e.start)} --> {_format_timestamp(e.end)}\n"
            )
            buf.write(f"{e.text}\n\n")
        return buf.getvalue()

    @staticmethod
    def _render_vtt(result: TranscriptResult) -> str:
        buf = io.StringIO()
        buf.write("WEBVTT\n\n")
        for e in result.entries:
            buf.write(
                f"{_format_timestamp(e.start, vtt=True)} --> "
                f"{_format_timestamp(e.end, vtt=True)}\n"
            )
            buf.write(f"{e.text}\n\n")
        return buf.getvalue()

    def _render_md(self, result: TranscriptResult) -> str:
        m = result.metadata
        lines = [
            f"# {m.title or m.video_id}",
            "",
            f"- **Channel:** {m.channel or '-'}",
            f"- **Video ID:** `{m.video_id}`",
            f"- **URL:** <{m.url}>",
            f"- **Language:** {result.language or '-'}",
            f"- **Auto-generated:** {result.is_generated}",
            f"- **Words:** {result.word_count}",
            "",
            "## Transcript",
            "",
        ]
        if self.config.include_timestamps:
            for e in result.entries:
                lines.append(f"- `[{_format_timestamp(e.start)}]` {e.text}")
        else:
            lines.append(result.plain_text)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_csv(result: TranscriptResult) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        w.writerow(["index", "start", "end", "duration", "text"])
        for i, e in enumerate(result.entries):
            w.writerow([i, f"{e.start:.3f}", f"{e.end:.3f}", f"{e.duration:.3f}", e.text])
        return buf.getvalue()

    # ---------- writing ----------

    def write(self, result: TranscriptResult, fmt: str) -> Path:
        """Write a single-video output file and return the path."""
        stem = _sanitize(
            f"{result.metadata.video_id}_{result.metadata.title}"
            if result.metadata.title
            else result.metadata.video_id,
            fallback=result.metadata.video_id,
        )
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.output_dir / f"{stem}.{fmt}"
        path.write_text(self.render(result, fmt), encoding="utf-8")
        return path

    def write_combined(
        self,
        results: Iterable[TranscriptResult],
        fmt: str,
        filename: str = "combined",
    ) -> Path:
        """Write many transcripts into one file with clear separators."""
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.output_dir / f"{filename}.{fmt}"
        sep = "\n\n" + ("=" * 80) + "\n\n"
        parts: list[str] = []
        for r in results:
            if r.success:
                parts.append(self.render(r, fmt))
        path.write_text(sep.join(parts), encoding="utf-8")
        return path
