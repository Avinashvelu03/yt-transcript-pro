"""Tests for data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata


class TestTranscriptEntry:
    def test_basic(self) -> None:
        e = TranscriptEntry(text="hi", start=1.0, duration=2.0)
        assert e.end == 3.0

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            TranscriptEntry(text="x", start=-1.0, duration=1.0)
        with pytest.raises(ValidationError):
            TranscriptEntry(text="x", start=0.0, duration=-1.0)


class TestVideoMetadata:
    def test_valid(self, sample_meta: VideoMetadata) -> None:
        assert sample_meta.video_id == "abcdefghijk"

    @pytest.mark.parametrize("bad", ["", "too_short", "way_too_long_xxxxx"])
    def test_invalid_video_id(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            VideoMetadata(video_id=bad)


class TestTranscriptResult:
    def test_plain_text_and_word_count(self, sample_meta: VideoMetadata) -> None:
        entries = [
            TranscriptEntry(text="hello world", start=0.0, duration=1.0),
            TranscriptEntry(text="foo bar baz", start=1.0, duration=1.0),
            TranscriptEntry(text="", start=2.0, duration=1.0),
        ]
        r = TranscriptResult(metadata=sample_meta, entries=entries, language="en")
        assert r.plain_text == "hello world\nfoo bar baz"
        assert r.word_count == 5

    def test_defaults(self, sample_meta: VideoMetadata) -> None:
        r = TranscriptResult(metadata=sample_meta)
        assert r.success is True
        assert r.entries == []
        assert r.language is None
        assert r.fetched_at is not None
