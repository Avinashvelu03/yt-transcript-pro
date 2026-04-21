"""Typed data models used throughout yt-transcript-pro."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class TranscriptEntry(BaseModel):
    """A single line/segment of a transcript."""

    text: str
    start: float = Field(ge=0.0)
    duration: float = Field(ge=0.0)

    @property
    def end(self) -> float:
        """End timestamp in seconds."""
        return self.start + self.duration


class VideoMetadata(BaseModel):
    """Metadata for a YouTube video."""

    video_id: str
    title: str = ""
    channel: str = ""
    channel_id: str = ""
    upload_date: str | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    url: str = ""

    @field_validator("video_id")
    @classmethod
    def _validate_video_id(cls, v: str) -> str:
        if not v or len(v) != 11:
            raise ValueError(f"Invalid YouTube video_id: {v!r}")
        return v


class TranscriptResult(BaseModel):
    """Result of attempting to extract a transcript."""

    metadata: VideoMetadata
    entries: list[TranscriptEntry] = Field(default_factory=list)
    language: str | None = None
    is_generated: bool = False
    success: bool = True
    error: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def plain_text(self) -> str:
        """Concatenate entry texts into a single plain-text block."""
        return "\n".join(e.text for e in self.entries if e.text)

    @property
    def word_count(self) -> int:
        return len(self.plain_text.split())
