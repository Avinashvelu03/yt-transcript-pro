"""Tests for format writers."""

from __future__ import annotations

import json

import pytest

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata
from yt_transcript_pro.writers import FormatWriter, _format_timestamp, _sanitize


@pytest.fixture()
def sample_result() -> TranscriptResult:
    meta = VideoMetadata(
        video_id="abcdefghijk",
        title="My Title / Weird:Chars?",
        channel="ChanName",
        url="https://youtu.be/abcdefghijk",
    )
    entries = [
        TranscriptEntry(text="hello", start=0.0, duration=1.5),
        TranscriptEntry(text="world", start=1.5, duration=2.0),
    ]
    return TranscriptResult(
        metadata=meta, entries=entries, language="en", is_generated=False
    )


class TestHelpers:
    def test_sanitize(self) -> None:
        assert _sanitize("a/b c.txt", "fb") == "a_b_c.txt"
        assert _sanitize("", "fb") == "fb"
        assert _sanitize("///", "fb") == "fb"
        long_name = "x" * 300
        assert len(_sanitize(long_name, "fb")) == 120

    def test_format_timestamp_srt(self) -> None:
        assert _format_timestamp(0.0) == "00:00:00,000"
        assert _format_timestamp(3661.5) == "01:01:01,500"

    def test_format_timestamp_vtt(self) -> None:
        assert _format_timestamp(1.25, vtt=True) == "00:00:01.250"

    def test_format_timestamp_negative_clamped(self) -> None:
        assert _format_timestamp(-5) == "00:00:00,000"

    def test_format_timestamp_ms_rounding(self) -> None:
        # 0.9995s → 1.000s (ms rolls over)
        assert _format_timestamp(0.9995) == "00:00:01,000"


class TestRender:
    def test_txt_with_header(self, sample_result: TranscriptResult) -> None:
        w = FormatWriter(Config(include_timestamps=False, include_metadata_header=True))
        out = w.render(sample_result, "txt")
        assert "# Title:" in out
        assert "hello" in out and "world" in out

    def test_txt_without_header_with_timestamps(
        self, sample_result: TranscriptResult
    ) -> None:
        w = FormatWriter(
            Config(include_timestamps=True, include_metadata_header=False)
        )
        out = w.render(sample_result, "txt")
        assert "#" not in out.splitlines()[0]
        assert "[00:00:00,000] hello" in out

    def test_json(self, sample_result: TranscriptResult) -> None:
        w = FormatWriter(Config())
        data = json.loads(w.render(sample_result, "json"))
        assert data["metadata"]["video_id"] == "abcdefghijk"
        assert len(data["entries"]) == 2

    def test_srt(self, sample_result: TranscriptResult) -> None:
        out = FormatWriter(Config()).render(sample_result, "srt")
        assert out.startswith("1\n")
        assert "00:00:00,000 --> 00:00:01,500" in out

    def test_vtt(self, sample_result: TranscriptResult) -> None:
        out = FormatWriter(Config()).render(sample_result, "vtt")
        assert out.startswith("WEBVTT")
        assert "00:00:00.000 --> 00:00:01.500" in out

    def test_md(self, sample_result: TranscriptResult) -> None:
        out = FormatWriter(Config()).render(sample_result, "md")
        assert out.startswith("# ")
        assert "## Transcript" in out

    def test_md_with_timestamps(self, sample_result: TranscriptResult) -> None:
        out = FormatWriter(Config(include_timestamps=True)).render(sample_result, "md")
        assert "`[00:00:00,000]`" in out

    def test_csv(self, sample_result: TranscriptResult) -> None:
        out = FormatWriter(Config()).render(sample_result, "csv")
        lines = out.strip().splitlines()
        assert lines[0].startswith("index,start,end,duration,text")
        assert len(lines) == 3

    def test_unknown_format_raises(self, sample_result: TranscriptResult) -> None:
        with pytest.raises(ValueError):
            FormatWriter(Config()).render(sample_result, "xml")


class TestWrite:
    def test_write_single(self, tmp_path, sample_result: TranscriptResult) -> None:
        w = FormatWriter(Config(output_dir=tmp_path))
        path = w.write(sample_result, "txt")
        assert path.exists()
        assert path.suffix == ".txt"
        content = path.read_text(encoding="utf-8")
        assert "hello" in content

    def test_write_single_no_title(self, tmp_path) -> None:
        meta = VideoMetadata(video_id="abcdefghijk")
        res = TranscriptResult(metadata=meta)
        w = FormatWriter(Config(output_dir=tmp_path))
        path = w.write(res, "txt")
        assert path.stem == "abcdefghijk"

    def test_write_combined(self, tmp_path, sample_result: TranscriptResult) -> None:
        meta2 = VideoMetadata(video_id="bbbbbbbbbbb")
        r2 = TranscriptResult(
            metadata=meta2,
            entries=[TranscriptEntry(text="another", start=0.0, duration=1.0)],
        )
        failed = TranscriptResult(
            metadata=VideoMetadata(video_id="ccccccccccc"),
            success=False,
            error="oops",
        )
        w = FormatWriter(Config(output_dir=tmp_path))
        path = w.write_combined([sample_result, r2, failed], "txt", "all")
        content = path.read_text(encoding="utf-8")
        assert "hello" in content
        assert "another" in content
        assert "=" * 20 in content
        # failed should be skipped
        assert "oops" not in content
