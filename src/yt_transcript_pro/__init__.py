"""yt-transcript-pro: production-grade YouTube transcript extractor.

Supports single videos, multi-video batches, entire playlists, and full
channels.  Ships with four extraction backends that can be combined:

* ``auto`` (default) — cascades ``watch`` → ``ytdlp`` → ``api`` per video.
  Bullet-proof for bulk jobs from any IP, including cloud.
* ``watch`` — scrapes the ``/watch?v=…`` HTML page.  Different anti-bot
  surface than the player API, works when Innertube is blocked.
* ``ytdlp`` — yt-dlp's player API with multi-client rotation
  (``android`` / ``android_vr`` / ``tv_simply`` / ``tv_embedded`` /
  ``mweb`` / ``web`` / ``ios``).
* ``api`` — legacy ``youtube-transcript-api`` backend (first to get
  rate-limited, kept for compatibility).

Author: Avinashvelu03
License: MIT
"""

from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor
from yt_transcript_pro.config import Config
from yt_transcript_pro.extractor import TranscriptExtractor
from yt_transcript_pro.models import (
    TranscriptEntry,
    TranscriptResult,
    VideoMetadata,
)
from yt_transcript_pro.resolver import SourceResolver
from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
from yt_transcript_pro.writers import FormatWriter
from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor

__version__ = "2.0.0"
__author__ = "Avinashvelu03"
__all__ = [
    "AutoTranscriptExtractor",
    "Config",
    "FormatWriter",
    "SourceResolver",
    "TranscriptEntry",
    "TranscriptExtractor",
    "TranscriptResult",
    "VideoMetadata",
    "WatchPageTranscriptExtractor",
    "YtDlpTranscriptExtractor",
    "__version__",
]
