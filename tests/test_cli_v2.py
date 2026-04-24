"""Additional CLI and writer tests for v2-specific code paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from yt_transcript_pro import cli as cli_module
from yt_transcript_pro.cli import app
from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata
from yt_transcript_pro.writers import FormatWriter

runner = CliRunner()


def _make_result(vid: str = "abcdefghijk", success: bool = True) -> TranscriptResult:
    meta = VideoMetadata(video_id=vid, url=f"https://youtu.be/{vid}")
    entries = (
        [TranscriptEntry(text="hello", start=0.0, duration=1.0)] if success else []
    )
    return TranscriptResult(
        metadata=meta,
        entries=entries,
        language="en" if success else None,
        success=success,
        error=None if success else "fail",
    )


# ---------------------------------------------------------------------------
# CLI: unknown backend
# ---------------------------------------------------------------------------


def test_extract_unknown_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    result = runner.invoke(
        app,
        [
            "extract",
            "abcdefghijk",
            "-o",
            str(tmp_path / "out"),
            "--no-resume",
            "--backend",
            "badbackend",
        ],
    )
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# CLI: watch backend
# ---------------------------------------------------------------------------


def test_extract_watch_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        r = _make_result()
        if progress:
            progress(1, 1, r)
        return [r]

    monkeypatch.setattr(
        cli_module.WatchPageTranscriptExtractor, "fetch_many", fake_fetch_many
    )

    outdir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "extract",
            "abcdefghijk",
            "-o",
            str(outdir),
            "-f",
            "txt",
            "--no-resume",
            "--backend",
            "watch",
        ],
    )
    assert result.exit_code == 0, result.stdout


# ---------------------------------------------------------------------------
# CLI: ytdlp backend
# ---------------------------------------------------------------------------


def test_extract_ytdlp_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        r = _make_result()
        if progress:
            progress(1, 1, r)
        return [r]

    monkeypatch.setattr(
        cli_module.YtDlpTranscriptExtractor, "fetch_many", fake_fetch_many
    )

    outdir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "extract",
            "abcdefghijk",
            "-o",
            str(outdir),
            "-f",
            "txt",
            "--no-resume",
            "--backend",
            "ytdlp",
        ],
    )
    assert result.exit_code == 0, result.stdout


# ---------------------------------------------------------------------------
# CLI: auto backend with custom order and player-clients
# ---------------------------------------------------------------------------


def test_extract_auto_backend_custom_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        r = _make_result()
        if progress:
            progress(1, 1, r)
        return [r]

    monkeypatch.setattr(
        cli_module.AutoTranscriptExtractor, "fetch_many", fake_fetch_many
    )

    outdir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "extract",
            "abcdefghijk",
            "-o",
            str(outdir),
            "-f",
            "txt",
            "--no-resume",
            "--backend",
            "auto",
            "--backend-order",
            "watch,api",
            "--player-clients",
            "android,web",
        ],
    )
    assert result.exit_code == 0, result.stdout


# ---------------------------------------------------------------------------
# CLI: combined with auto backend
# ---------------------------------------------------------------------------


def test_extract_combined_auto(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        r = _make_result()
        if progress:
            progress(1, 1, r)
        return [r]

    monkeypatch.setattr(
        cli_module.AutoTranscriptExtractor, "fetch_many", fake_fetch_many
    )

    outdir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "extract",
            "abcdefghijk",
            "-o",
            str(outdir),
            "-f",
            "txt",
            "-C",
            "--combined-name",
            "test_combined",
            "--no-resume",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (outdir / "test_combined.txt").exists()


# ---------------------------------------------------------------------------
# FormatWriter: append_combined
# ---------------------------------------------------------------------------


class TestAppendCombined:
    def test_append_combined_success(self, tmp_path: Path) -> None:
        cfg = Config(output_dir=tmp_path)
        writer = FormatWriter(cfg)
        r1 = _make_result("aaaaaaaaa01")
        r2 = _make_result("aaaaaaaaa02")

        path = writer.append_combined(r1, "txt", filename="combined")
        assert path.exists()
        first_size = path.stat().st_size
        assert first_size > 0

        path2 = writer.append_combined(r2, "txt", filename="combined")
        assert path2 == path
        assert path.stat().st_size > first_size
        content = path.read_text(encoding="utf-8")
        assert "=" * 80 in content

    def test_append_combined_skip_failure(self, tmp_path: Path) -> None:
        cfg = Config(output_dir=tmp_path)
        writer = FormatWriter(cfg)
        r_fail = _make_result(success=False)
        path = writer.append_combined(r_fail, "txt", filename="combined")
        assert not path.exists()  # no file created for failed results
