"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import VideoMetadata


@pytest.fixture()
def base_config(tmp_path) -> Config:
    return Config(
        concurrency=2,
        max_retries=1,
        retry_initial_delay=0.01,
        retry_max_delay=0.02,
        output_dir=tmp_path / "out",
        output_format="txt",
        resume=False,
    )


@pytest.fixture()
def sample_meta() -> VideoMetadata:
    return VideoMetadata(
        video_id="abcdefghijk",
        title="Hello World",
        channel="Test Channel",
        channel_id="UCxxxxxxxxxxxxxxxxxx",
        url="https://www.youtube.com/watch?v=abcdefghijk",
        upload_date="2024-01-01",
        duration_seconds=120,
        view_count=1000,
    )
