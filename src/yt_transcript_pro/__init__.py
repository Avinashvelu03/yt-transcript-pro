"""yt-transcript-pro: production-grade YouTube transcript extractor.

Supports single videos, multi-video batches, entire playlists, and full channels.
Author: Avinashvelu03
License: MIT
"""

from yt_transcript_pro.config import Config
from yt_transcript_pro.extractor import TranscriptExtractor
from yt_transcript_pro.models import (
    TranscriptEntry,
    TranscriptResult,
    VideoMetadata,
)
from yt_transcript_pro.resolver import SourceResolver
from yt_transcript_pro.writers import FormatWriter

__version__ = "1.0.0"
__author__ = "Avinashvelu03"
__all__ = [
    "Config",
    "FormatWriter",
    "SourceResolver",
    "TranscriptEntry",
    "TranscriptExtractor",
    "TranscriptResult",
    "VideoMetadata",
    "__version__",
]
