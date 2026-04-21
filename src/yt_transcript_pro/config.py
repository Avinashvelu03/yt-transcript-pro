"""Runtime configuration for yt-transcript-pro."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Runtime configuration.

    All fields are overridable via CLI flags.
    """

    # Concurrency & retry
    concurrency: int = 5
    max_retries: int = 4
    retry_initial_delay: float = 1.0
    retry_max_delay: float = 30.0
    request_timeout: float = 30.0

    # Language preferences (tried in order)
    languages: list[str] = field(default_factory=lambda: ["en", "en-US", "en-GB"])
    allow_generated: bool = True
    allow_translation: bool = True

    # Output
    output_dir: Path = Path("output")
    output_format: str = "txt"  # txt | json | srt | vtt | md | csv | all
    combine_into_single_file: bool = False
    include_timestamps: bool = False
    include_metadata_header: bool = True

    # Source limits
    max_videos: int | None = None

    # Networking / proxy support (see README "Working around IP bans")
    proxy: str | None = None  # http(s) proxy URL, e.g. http://user:pass@host:port
    webshare_proxy_username: str | None = None
    webshare_proxy_password: str | None = None
    cookies_file: Path | None = None
    user_agent: str | None = None

    # Behaviour
    resume: bool = True  # skip videos already extracted
    checkpoint_file: Path | None = None
    verbose: bool = False

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.max_videos is not None and self.max_videos < 1:
            raise ValueError("max_videos must be >= 1")
        if self.output_format not in {"txt", "json", "srt", "vtt", "md", "csv", "all"}:
            raise ValueError(f"Unsupported output_format: {self.output_format}")
