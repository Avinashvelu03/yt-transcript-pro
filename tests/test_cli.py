"""Tests for the CLI using typer.testing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from yt_transcript_pro import cli as cli_module
from yt_transcript_pro.cli import app
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata

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


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "yt-transcript-pro" in result.stdout


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_resolve_command(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", title="hi")

    def fake_resolve(self: Any, srcs: Any) -> list:
        return [meta]

    monkeypatch.setattr(cli_module.SourceResolver, "resolve", fake_resolve)
    result = runner.invoke(app, ["resolve", "dQw4w9WgXcQ"])
    assert result.exit_code == 0
    assert "abcdefghijk" in result.stdout


def test_resolve_command_with_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    metas = [VideoMetadata(video_id=f"aaaaaaaaa{i:02d}") for i in range(5)]
    monkeypatch.setattr(
        cli_module.SourceResolver, "resolve", lambda self, s: metas
    )
    result = runner.invoke(app, ["resolve", "X", "-n", "2"])
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2


def test_extract_no_videos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [])
    result = runner.invoke(
        app, ["extract", "bogus", "-o", str(tmp_path / "out"), "--no-resume"]
    )
    assert result.exit_code == 2


def test_extract_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        r = _make_result()
        if progress:
            progress(1, 1, r)
        return [r]

    monkeypatch.setattr(cli_module.TranscriptExtractor, "fetch_many", fake_fetch_many)

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
        ],
    )
    assert result.exit_code == 0, result.stdout
    files = list(outdir.glob("*.txt"))
    assert files, f"no files in {outdir}"


def test_extract_combined_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        r = _make_result()
        if progress:
            progress(1, 1, r)
        return [r]

    monkeypatch.setattr(cli_module.TranscriptExtractor, "fetch_many", fake_fetch_many)
    outdir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "extract",
            "abcdefghijk",
            "-o",
            str(outdir),
            "-f",
            "all",
            "-C",
            "--combined-name",
            "mega",
            "--no-resume",
            "-v",
        ],
    )
    assert result.exit_code == 0, result.stdout
    for ext in ("txt", "json", "srt", "vtt", "md", "csv"):
        assert (outdir / f"mega.{ext}").exists()


def test_extract_with_resume_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        assert videos == []  # resume skipped everything
        return []

    monkeypatch.setattr(cli_module.TranscriptExtractor, "fetch_many", fake_fetch_many)

    # Pre-populate checkpoint
    outdir = tmp_path / "out"
    outdir.mkdir()
    ckpt = outdir / ".yttp-checkpoint.json"
    ckpt.write_text('{"done": ["abcdefghijk"]}')

    result = runner.invoke(
        app, ["extract", "abcdefghijk", "-o", str(outdir), "--resume"]
    )
    assert result.exit_code == 0
    assert "skipping 1 already completed" in result.stdout


def test_extract_max_videos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    metas = [VideoMetadata(video_id=f"aaaaaaaaa{i:02d}") for i in range(5)]
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: metas)

    captured: dict[str, Any] = {}

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        captured["n"] = len(videos)
        return []

    monkeypatch.setattr(cli_module.TranscriptExtractor, "fetch_many", fake_fetch_many)
    runner.invoke(
        app,
        ["extract", "X", "-o", str(tmp_path / "o"), "-n", "3", "--no-resume"],
    )
    assert captured["n"] == 3


def test_formats_to_write() -> None:
    from yt_transcript_pro.cli import _formats_to_write

    assert _formats_to_write("txt") == ["txt"]
    assert len(_formats_to_write("all")) == 6


def test_extract_writes_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Run with resume → checkpoint should be saved and individual files written
    metas = [
        VideoMetadata(video_id=f"aaaaaaaaa{i:02d}", url="u") for i in range(12)
    ]
    monkeypatch.setattr(
        cli_module.SourceResolver, "resolve", lambda self, s: metas
    )

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        results = []
        total = len(videos)
        for i, v in enumerate(videos, start=1):
            r = TranscriptResult(
                metadata=v,
                entries=[TranscriptEntry(text="x", start=0.0, duration=1.0)],
                language="en",
                success=True,
            )
            results.append(r)
            if progress:
                progress(i, total, r)
        return results

    monkeypatch.setattr(
        cli_module.TranscriptExtractor, "fetch_many", fake_fetch_many
    )

    outdir = tmp_path / "out"
    result = runner.invoke(
        app, ["extract", "X", "-o", str(outdir), "--resume"]
    )
    assert result.exit_code == 0, result.stdout
    ckpt = outdir / ".yttp-checkpoint.json"
    assert ckpt.exists()
    data = ckpt.read_text()
    assert "aaaaaaaaa00" in data
    # At least some individual per-video files exist
    assert list(outdir.glob("aaaaaaaaa*.txt"))
