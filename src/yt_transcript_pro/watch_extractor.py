"""Third extraction backend: scrape the public ``/watch?v=…`` HTML page.

Why a third backend?
--------------------
``ytdlp_extractor`` uses YouTube's player API (Innertube) via yt-dlp's
client pool.  That API is now the primary surface for anti-bot
enforcement — once YouTube flags an IP, every Innertube call on that IP
gets a ``LOGIN_REQUIRED`` / "Sign in to confirm you're not a bot"
response, regardless of client.

The plain ``https://www.youtube.com/watch?v=<id>`` HTML page is served
by a **different** fleet with different heuristics.  For channels with
public, non-age-restricted videos it almost always returns a full
``ytInitialPlayerResponse`` object containing the caption tracks,
**even when the Innertube endpoints are 429ing for the same IP**.

This backend:

1. GETs the watch page with realistic browser headers and consent cookies.
2. Parses the ``ytInitialPlayerResponse`` JSON blob out of the HTML.
3. Picks the best caption track per the Config's language preferences.
4. Downloads the caption file (json3/xml/vtt) and parses it with the
   same parsers used by :mod:`ytdlp_extractor`.

It is a genuine drop-in replacement — same method surface as both other
extractors — so the CLI simply switches on ``--backend watch``.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, cast
from zlib import MAX_WBITS, decompress

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptResult, VideoMetadata
from yt_transcript_pro.ytdlp_extractor import (
    _USER_AGENTS,
    _classify_error,
    _pick_browser_profile,
    parse_subtitle,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, TranscriptResult], None]


_PLAYER_RESPONSE_RE = re.compile(
    r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var|</script>)", re.DOTALL
)
# Fallback pattern: sometimes emitted inside an inline JSON assignment
_PLAYER_RESPONSE_RE2 = re.compile(
    r'"playerResponse"\s*:\s*"(?P<escaped>(?:\\.|[^"\\])+)"', re.DOTALL
)


def _pick_user_agent() -> str:
    return random.choice(_USER_AGENTS)  # nosec B311


def _open_gzip(resp_raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if encoding == "gzip":
        try:
            return gzip.decompress(resp_raw)
        except OSError:
            return resp_raw
    if encoding == "deflate":
        try:
            return decompress(resp_raw)
        except Exception:
            try:
                return decompress(resp_raw, -MAX_WBITS)
            except Exception:
                return resp_raw
    return resp_raw


def _http_get(url: str, *, timeout: float, ua: str, cookies: dict[str, str] | None = None) -> bytes:
    # Use a full browser profile for coherent fingerprinting
    profile = _pick_browser_profile()
    # Override UA if caller specified one
    if ua:
        profile["User-Agent"] = ua
    headers = {
        "User-Agent": profile["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": profile["Accept-Language"],
        "Accept-Encoding": "gzip, deflate",
        "Sec-Fetch-Mode": profile.get("Sec-Fetch-Mode", "navigate"),
        "Sec-Fetch-Site": profile.get("Sec-Fetch-Site", "none"),
        "Sec-Fetch-User": profile.get("Sec-Fetch-User", "?1"),
        "Sec-Fetch-Dest": profile.get("Sec-Fetch-Dest", "document"),
        "Upgrade-Insecure-Requests": "1",
    }
    # Add sec-ch-ua Client Hints if present (Chrome/Edge browsers)
    for key in ("sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
                "sec-ch-ua-platform-version", "sec-ch-ua-arch",
                "sec-ch-ua-bitness", "sec-ch-ua-full-version-list"):
        if key in profile:
            headers[key] = profile[key]
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        raw = resp.read()
        enc = resp.headers.get("Content-Encoding") or ""
    return _open_gzip(cast(bytes, raw), enc)


def _default_cookies() -> dict[str, str]:
    """Cookies that pre-accept Google's EU consent page.

    Without these, some regions get redirected to ``consent.youtube.com``
    before the watch page renders, which breaks scraping.
    """
    return {
        "CONSENT": "YES+cb.20210328-17-p0.en+FX+999",
        "SOCS": "CAI",
        "PREF": "hl=en&tz=UTC",
    }


def _extract_player_response(html: str) -> dict[str, Any] | None:
    m = _PLAYER_RESPONSE_RE.search(html)
    if m:
        try:
            payload = json.loads(m.group(1))
            return cast(dict[str, Any], payload) if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            pass
    # Fallback: JSON-escaped variant embedded in ytcfg
    m2 = _PLAYER_RESPONSE_RE2.search(html)
    if m2:
        # The string is a JSON-escaped JSON string — decode it twice
        try:
            unescaped = json.loads(f'"{m2.group("escaped")}"')
            payload = json.loads(unescaped)
            return cast(dict[str, Any], payload) if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            pass
    return None


class WatchPageTranscriptExtractor:
    """Extract transcripts by scraping the ``/watch`` HTML page.

    Has the same ``fetch_one`` / ``fetch_one_async`` / ``fetch_many``
    surface as the other two extractors so it can be swapped at will.
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._cookies: dict[str, str] = dict(_default_cookies())
        self._throttle_lock: asyncio.Lock | None = None
        self._throttle_backoff: float = 0.0
        self._consecutive_throttles: int = 0

    # ---------- core ----------

    def fetch_one(self, meta: VideoMetadata) -> TranscriptResult:
        vid = meta.video_id
        url = f"https://www.youtube.com/watch?v={vid}&hl=en&gl=US"
        ua = self.config.user_agent or _pick_user_agent()
        try:
            html_bytes = _http_get(
                url, timeout=self.config.request_timeout, ua=ua, cookies=self._cookies
            )
        except urllib.error.HTTPError as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"HTTPError {exc.code} on watch page",
            )
        except Exception as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"watch fetch failed: {type(exc).__name__}: {exc}",
            )
        html = html_bytes.decode("utf-8", errors="replace")

        if "confirm you" in html.lower() and "not a bot" in html.lower() and "ytInitialPlayerResponse" not in html:
            return TranscriptResult(
                metadata=meta, success=False, error="watch page returned bot challenge"
            )

        pr = _extract_player_response(html)
        if pr is None:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error="ytInitialPlayerResponse not found in watch page HTML",
            )

        playability = pr.get("playabilityStatus") or {}
        status = playability.get("status")
        if status in {"UNPLAYABLE", "ERROR", "LOGIN_REQUIRED"}:
            reason = playability.get("reason") or status
            return TranscriptResult(metadata=meta, success=False, error=f"{status}: {reason}")

        # Merge video metadata from the page
        details = pr.get("videoDetails") or {}
        meta = meta.model_copy(
            update={
                "title": meta.title or str(details.get("title") or ""),
                "channel": meta.channel or str(details.get("author") or ""),
                "channel_id": meta.channel_id or str(details.get("channelId") or ""),
                "duration_seconds": meta.duration_seconds
                or (int(details["lengthSeconds"]) if details.get("lengthSeconds", "").isdigit() else None),
                "view_count": meta.view_count
                or (int(details["viewCount"]) if str(details.get("viewCount", "")).isdigit() else None),
                "url": meta.url or f"https://www.youtube.com/watch?v={vid}",
            }
        )

        tracks = (
            (pr.get("captions") or {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        if not tracks:
            return TranscriptResult(
                metadata=meta, success=False, error="no captions available for this video"
            )

        picked = self._pick_track(tracks)
        if picked is None:
            langs = sorted({t.get("languageCode") for t in tracks})
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"no preferred-language captions (available: {langs})",
            )

        lang_code, track, is_generated = picked
        # Force json3 format for easy parsing
        cap_url = self._add_query(track["baseUrl"], {"fmt": "json3"})
        try:
            cap_bytes = _http_get(
                cap_url, timeout=self.config.request_timeout, ua=ua, cookies=self._cookies
            )
        except Exception as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"caption download failed: {type(exc).__name__}: {exc}",
            )
        entries = parse_subtitle(cap_bytes, "json3")
        if not entries:
            return TranscriptResult(
                metadata=meta, success=False, error="empty or unparseable caption payload"
            )
        return TranscriptResult(
            metadata=meta,
            entries=entries,
            language=lang_code,
            is_generated=is_generated,
            success=True,
        )

    # ---------- track picker ----------

    def _pick_track(
        self, tracks: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any], bool] | None:
        langs = self.config.languages
        # 1) manually-created, exact match
        for lang in langs:
            for t in tracks:
                if t.get("languageCode") == lang and t.get("kind") != "asr":
                    return lang, t, False
        # 2) auto-generated, exact match
        if self.config.allow_generated:
            for lang in langs:
                for t in tracks:
                    if t.get("languageCode") == lang and t.get("kind") == "asr":
                        return lang, t, True
        # 3) prefix match (en-US matches "en")
        for lang in langs:
            for t in tracks:
                code = str(t.get("languageCode") or "")
                if code.startswith(lang):
                    return code, t, t.get("kind") == "asr"
        # 4) any English-ish track
        for t in tracks:
            code = str(t.get("languageCode") or "").lower()
            if code.startswith("en"):
                return t.get("languageCode", "en"), t, t.get("kind") == "asr"
        # 5) translatable → first track, translated to the first preferred lang
        if self.config.allow_translation:
            for t in tracks:
                if t.get("isTranslatable"):
                    # add translation query param
                    new_t = dict(t)
                    new_t["baseUrl"] = self._add_query(
                        t["baseUrl"], {"tlang": langs[0]}
                    )
                    return f"{langs[0]} (translated)", new_t, True
        return None

    @staticmethod
    def _add_query(url: str, params: dict[str, str]) -> str:
        if url.startswith("//"):
            url = f"https:{url}"
        elif url.startswith("/"):
            url = f"https://www.youtube.com{url}"
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        query.update(params)
        new_q = urllib.parse.urlencode(query)
        return urllib.parse.urlunparse(parsed._replace(query=new_q))

    # ---------- async with adaptive backoff ----------

    async def fetch_one_async(self, meta: VideoMetadata) -> TranscriptResult:
        if self._throttle_lock is None:
            self._throttle_lock = asyncio.Lock()
        async with self._throttle_lock:
            delay = self._throttle_backoff
        if delay > 0:
            await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))  # nosec B311
        await asyncio.sleep(0.05 + 0.25 * random.random())  # nosec B311

        attempts = max(self.config.max_retries + 1, 1)
        last: TranscriptResult | None = None
        for attempt in range(attempts):
            last = await asyncio.to_thread(self.fetch_one, meta)
            if last.success:
                await self._note_success()
                return last
            low = (last.error or "").lower()
            if _classify_error(last.error or "") == "permanent":
                return last
            if (
                "bot" in low or "429" in low or "login_required" in low
                or "sign in" in low
            ):
                await self._note_throttle()
            if attempt < attempts - 1:
                base = self.config.retry_initial_delay * (2**attempt)
                backoff = min(base, self.config.retry_max_delay)
                backoff *= 0.7 + 0.6 * random.random()  # nosec B311
                await asyncio.sleep(backoff)
        return last or TranscriptResult(metadata=meta, success=False, error="no attempts")

    def _lock(self) -> asyncio.Lock:
        if self._throttle_lock is None:
            self._throttle_lock = asyncio.Lock()
        return self._throttle_lock

    async def _note_success(self) -> None:
        async with self._lock():
            self._consecutive_throttles = 0
            self._throttle_backoff = max(0.0, self._throttle_backoff * 0.7 - 0.25)

    async def _note_throttle(self) -> None:
        async with self._lock():
            self._consecutive_throttles += 1
            # Grow backoff, but cap it tightly (15s) so the circuit breaker
            # in ``AutoTranscriptExtractor`` can disable this backend fast
            # rather than making workers sleep 60s each.
            new = max(self._throttle_backoff * 1.3, 1.0) + self._consecutive_throttles * 0.5
            self._throttle_backoff = min(new, 15.0)
            if self._consecutive_throttles % 5 == 1:
                logger.info(
                    "⚠ watch-backend throttle — next worker waits %.1fs (strike %d)",
                    self._throttle_backoff,
                    self._consecutive_throttles,
                )

    # ---------- batch ----------

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
                    except Exception as exc:
                        logger.warning("progress callback raised: %s", exc)
            return res

        tasks = [asyncio.create_task(worker(m)) for m in videos]
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
        return results
