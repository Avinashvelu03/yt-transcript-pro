"""Tests for Config validation."""

from __future__ import annotations

import pytest

from yt_transcript_pro.config import Config


def test_defaults_valid() -> None:
    c = Config()
    assert c.concurrency >= 1
    assert c.max_retries >= 0
    assert "en" in c.languages


@pytest.mark.parametrize(
    "kwargs",
    [
        {"concurrency": 0},
        {"max_retries": -1},
        {"max_videos": 0},
        {"output_format": "bogus"},
    ],
)
def test_invalid(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        Config(**kwargs)


def test_max_videos_none_allowed() -> None:
    c = Config(max_videos=None)
    assert c.max_videos is None


def test_all_formats_valid() -> None:
    for f in ["txt", "json", "srt", "vtt", "md", "csv", "all"]:
        Config(output_format=f)
