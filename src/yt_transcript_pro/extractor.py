"""Concurrent, resilient transcript fetcher.

Built against youtube-transcript-api >= 1.0 (instance-based API).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api._errors import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    IpBlocked,
    NotTranslatable,
    RequestBlocked,
    TranslationLanguageNotAvailable,
    VideoUnplayable,
)

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata

logger = logging.getLogger(__name__)

# Permanent errors — no point retrying these.
# IpBlocked/RequestBlocked are included because retrying from a blocked IP
# won't succeed; callers should configure a proxy instead.
_PERMANENT = (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    AgeRestricted,
    InvalidVideoId,
    NotTranslatable,
    TranslationLanguageNotAvailable,
    IpBlocked,
    RequestBlocked,
)

# Progress callback signature: (completed, total, current_result)
ProgressCallback = Callable[[int, int, TranscriptResult], None]


class TranscriptExtractor:
    """High-throughput transcript extractor with retries and concurrency control."""

    def __init__(
        self,
        config: Config | None = None,
        api: Any | None = None,
    ) -> None:
        self.config = config or Config()
        self._api = api if api is not None else self._build_default_api()

    def _build_default_api(self) -> Any:
        """Build a YouTubeTranscriptApi instance, optionally with proxy config."""
        from youtube_transcript_api.proxies import (
            GenericProxyConfig,
            WebshareProxyConfig,
        )

        cfg = self.config
        if cfg.webshare_proxy_username and cfg.webshare_proxy_password:
            return YouTubeTranscriptApi(
                proxy_config=WebshareProxyConfig(
                    proxy_username=cfg.webshare_proxy_username,
                    proxy_password=cfg.webshare_proxy_password,
                )
            )
        if cfg.proxy:
            return YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(
                    http_url=cfg.proxy, https_url=cfg.proxy
                )
            )
        return YouTubeTranscriptApi()

    # ---------- low-level single video ----------

    def fetch_one(self, meta: VideoMetadata) -> TranscriptResult:
        """Fetch transcript for a single video (sync)."""
        try:
            transcript_list = self._api.list(meta.video_id)
            transcript = self._select_transcript(transcript_list, meta.video_id)
            fetched = transcript.fetch()
            # Support both old (list of dict) and new (FetchedTranscript) return types
            raw_iter = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
            entries = [
                TranscriptEntry(
                    text=str(item.get("text", "")),
                    start=float(item.get("start", 0.0)),
                    duration=float(item.get("duration", 0.0)),
                )
                for item in raw_iter
            ]
            return TranscriptResult(
                metadata=meta,
                entries=entries,
                language=getattr(transcript, "language_code", None),
                is_generated=getattr(transcript, "is_generated", False),
                success=True,
            )
        except _PERMANENT as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        except CouldNotRetrieveTranscript:
            # Retryable upstream; re-raise so tenacity can decide
            raise
        except Exception as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _select_transcript(self, transcript_list: Any, video_id: str) -> Any:
        """Pick the best transcript per config preferences."""
        langs = self.config.languages
        # Preferred: manually-created transcript in preferred language
        try:
            return transcript_list.find_manually_created_transcript(langs)
        except NoTranscriptFound:
            pass
        if self.config.allow_generated:
            try:
                return transcript_list.find_generated_transcript(langs)
            except NoTranscriptFound:
                pass
        # Fall back: any transcript, optionally translated
        try:
            for t in transcript_list:
                if self.config.allow_translation and getattr(t, "is_translatable", False):
                    try:
                        return t.translate(langs[0])
                    except Exception as translate_exc:
                        logger.debug(
                            "translation to %s failed for %s: %s",
                            langs[0],
                            video_id,
                            translate_exc,
                        )
                        continue
                return t
        except Exception as exc:
            raise NoTranscriptFound(
                video_id=video_id,
                requested_language_codes=langs,
                transcript_data=transcript_list,
            ) from exc
        raise NoTranscriptFound(
            video_id=video_id,
            requested_language_codes=langs,
            transcript_data=transcript_list,
        )

    # ---------- async with retries ----------

    async def fetch_one_async(self, meta: VideoMetadata) -> TranscriptResult:
        """Async fetch with exponential-backoff retries on transient errors."""
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self.config.retry_initial_delay,
                    max=self.config.retry_max_delay,
                ),
                retry=retry_if_exception_type(CouldNotRetrieveTranscript),
                reraise=True,
            ):
                with attempt:
                    return await asyncio.to_thread(self.fetch_one, meta)
        except RetryError as exc:  # pragma: no cover
            return TranscriptResult(
                metadata=meta, success=False, error=f"RetryError: {exc}"
            )
        except CouldNotRetrieveTranscript as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        return TranscriptResult(  # pragma: no cover
            metadata=meta, success=False, error="no attempts made"
        )

    # ---------- batch ----------

    async def fetch_many(
        self,
        videos: list[VideoMetadata],
        progress: ProgressCallback | None = None,
    ) -> list[TranscriptResult]:
        """Fetch transcripts for many videos concurrently."""
        if not videos:
            return []
        sem = asyncio.Semaphore(self.config.concurrency)
        total = len(videos)
        results: list[TranscriptResult] = []
        counter = {"done": 0}
        lock = asyncio.Lock()

        async def worker(meta: VideoMetadata) -> TranscriptResult:
            async with sem:
                res = await self.fetch_one_async(meta)
            async with lock:
                counter["done"] += 1
                if progress is not None:
                    progress(counter["done"], total, res)
            return res

        tasks = [asyncio.create_task(worker(m)) for m in videos]
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
        return results
