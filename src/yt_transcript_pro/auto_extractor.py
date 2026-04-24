"""Auto-fallback extractor: chains all available backends per video.

``AutoTranscriptExtractor`` is the recommended default for bulk
extractions from cloud IPs or any environment where YouTube anti-bot
heuristics are aggressive.  For each video it tries the backends in the
following order, moving to the next only if the previous returns a
transient / anti-bot failure:

1. **``watch``** — scrape ``/watch?v=…`` HTML.  Very reliable for
   public, non-age-restricted videos.
2. **``ytdlp``** — yt-dlp with client rotation (``android`` /
   ``android_vr`` / ``tv_simply`` / ``tv_embedded`` / ``mweb`` / ``web``
   / ``ios``).  Works even when the watch page is blocked but the
   player API is still accessible.
3. **``api``** — legacy ``youtube-transcript-api``.  Kept as a last
   resort; it's the first thing YouTube rate-limits, but still works
   from fresh residential IPs.

Each backend is tried **independently** for each failed video, so a
single anti-bot blip on backend #1 doesn't poison the whole run.
Successful results short-circuit the chain.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Callable

from yt_transcript_pro.config import Config
from yt_transcript_pro.extractor import TranscriptExtractor
from yt_transcript_pro.models import TranscriptResult, VideoMetadata
from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
from yt_transcript_pro.ytdlp_extractor import (
    YtDlpTranscriptExtractor,
    _classify_error,
)

# Circuit-breaker thresholds: after this many *consecutive* non-permanent
# failures, the backend is temporarily disabled and the cascade skips it.
# The breaker resets on the next successful fetch via that backend.
_BREAKER_THRESHOLD = 15

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, TranscriptResult], None]


class AutoTranscriptExtractor:
    """Cascade over watch → ytdlp → api.

    The three sub-extractors each have their own adaptive throttle
    state, which is crucial: when one backend gets rate-limited, its
    cooperative sleeps slow down just that backend, while the others
    keep running at full speed.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        backend_order: Iterable[str] | None = None,
    ) -> None:
        self.config = config or Config()
        self._watch = WatchPageTranscriptExtractor(self.config)
        self._ytdlp = YtDlpTranscriptExtractor(self.config)
        self._api = TranscriptExtractor(self.config)
        # Default order chosen to maximise success rate on a mix of fresh
        # and flagged IPs: ``ytdlp`` first because its multi-client pool
        # has the highest per-IP quota; ``watch`` second for its separate
        # frontend fleet; ``api`` as last resort.
        self.backend_order: tuple[str, ...] = (
            tuple(backend_order) if backend_order else ("ytdlp", "watch", "api")
        )
        # Circuit-breaker counters per backend.
        self._breaker_failures: dict[str, int] = {b: 0 for b in self.backend_order}
        self._breaker_lock: asyncio.Lock | None = None

    # ---------- sync ----------

    def fetch_one(self, meta: VideoMetadata) -> TranscriptResult:
        last: TranscriptResult | None = None
        for backend in self.backend_order:
            if self._breaker_failures.get(backend, 0) >= _BREAKER_THRESHOLD:
                logger.debug("auto: skipping %s (breaker open)", backend)
                continue
            ext = self._get(backend)
            res = ext.fetch_one(meta)
            if res.success:
                logger.debug("auto: %s succeeded via %s", meta.video_id, backend)
                self._breaker_failures[backend] = 0
                return res
            last = res
            if _classify_error(res.error or "") == "permanent":
                return res
            self._breaker_failures[backend] = self._breaker_failures.get(backend, 0) + 1
        return last or TranscriptResult(
            metadata=meta, success=False, error="all backends failed"
        )

    def _get(self, name: str) -> object:
        return {"watch": self._watch, "ytdlp": self._ytdlp, "api": self._api}[name]

    # ---------- async ----------

    async def fetch_one_async(self, meta: VideoMetadata) -> TranscriptResult:
        if self._breaker_lock is None:
            self._breaker_lock = asyncio.Lock()
        last: TranscriptResult | None = None
        for backend in self.backend_order:
            async with self._breaker_lock:
                if self._breaker_failures.get(backend, 0) >= _BREAKER_THRESHOLD:
                    logger.debug("auto: skipping %s (breaker open)", backend)
                    continue
            ext = self._get(backend)
            if backend == "watch":
                res = await self._watch.fetch_one_async(meta)
            elif backend == "ytdlp":
                res = await self._ytdlp.fetch_one_async(meta)
            else:
                res = await self._api.fetch_one_async(meta)
            if res.success:
                async with self._breaker_lock:
                    self._breaker_failures[backend] = 0
                return res
            last = res
            if _classify_error(res.error or "") == "permanent":
                return res
            async with self._breaker_lock:
                self._breaker_failures[backend] = (
                    self._breaker_failures.get(backend, 0) + 1
                )
                if self._breaker_failures[backend] == _BREAKER_THRESHOLD:
                    logger.warning(
                        "Backend %r has failed %d times in a row — disabling for "
                        "the rest of this run. Will re-enable if another backend "
                        "eventually succeeds on any video.",
                        backend,
                        _BREAKER_THRESHOLD,
                    )
        return last or TranscriptResult(
            metadata=meta, success=False, error="all backends failed"
        )

    # ---------- batch (shared scheduler) ----------

    async def fetch_many(
        self,
        videos: list[VideoMetadata],
        progress: ProgressCallback | None = None,
    ) -> list[TranscriptResult]:
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
                    try:
                        progress(counter["done"], total, res)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("progress callback raised: %s", exc)
            return res

        tasks = [asyncio.create_task(worker(m)) for m in videos]
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
        return results
