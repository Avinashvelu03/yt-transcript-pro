"""High-resilience transcript extractor powered by yt-dlp.

Why this exists
---------------
The original ``extractor.py`` uses ``youtube-transcript-api``, which calls
YouTube's ``/api/timedtext`` endpoint directly with a minimal request.
YouTube aggressively IP-blocks any address that hits that endpoint in
bulk, returning ``IpBlocked`` / ``RequestBlocked`` errors.

``yt-dlp`` goes through the same player API the YouTube website, Android
app, mobile web, and TV embeds use, and rotates among multiple player
clients with full request fingerprints (headers, client versions, etc.).
That means YouTube's abuse heuristics treat it like normal user traffic,
and it keeps working long after ``youtube-transcript-api`` gets blocked.

This module wraps yt-dlp's extractor with:

* **Multi-client fallback** (android / web / mweb / ios / tv_embedded)
* **Rotating modern User-Agents**
* **Exponential backoff with jitter** on transient errors
* **Native parsing of json3 / vtt / srv3 / ttml / srv1 subtitles**
* **Adaptive concurrency** — on repeated throttle signals it halves itself
* **Optional cookies.txt support** for age-restricted/private content
* **Zero dependency on external proxies** — works on bare residential
  *and* cloud IPs (verified on Google Cloud and DigitalOcean ranges that
  normally get blocked instantly by ``youtube-transcript-api``).

Public surface mirrors ``TranscriptExtractor`` so it is a drop-in
replacement: ``fetch_one`` / ``fetch_one_async`` / ``fetch_many``.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import random
import re
import urllib.error
import urllib.request
from collections.abc import Iterable
from typing import Any, Callable, cast
from zlib import MAX_WBITS, decompress

from defusedxml import ElementTree
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, TranscriptResult], None]


class _SilentLogger:
    """Drop yt-dlp's chatter into the module logger at DEBUG level.

    Without this, yt-dlp writes ``ERROR: [youtube] …`` lines to stderr
    for every expected client-fallback attempt, drowning the user's
    progress bar. We still keep the messages in the debug log so
    ``--verbose`` users can diagnose issues.
    """

    def debug(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        logger.debug("yt-dlp warn: %s", msg)

    def error(self, msg: str) -> None:
        logger.debug("yt-dlp error: %s", msg)


# ---------------------------------------------------------------------------
# Browser Fingerprint Profiles — rotated per request.
#
# YouTube cross-checks User-Agent against sec-ch-ua, sec-ch-ua-platform,
# and other Client Hints headers. Sending a Chrome UA without the matching
# sec-ch-ua header is an instant bot signal.  Each profile below is a
# coherent set of headers captured from a real browser.
# ---------------------------------------------------------------------------

_BROWSER_PROFILES: tuple[dict[str, str], ...] = (
    # ---- Chrome 147 on Windows (from user's actual browser) ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version-list": (
            '"Google Chrome";v="147.0.7727.102", '
            '"Not.A/Brand";v="8.0.0.0", '
            '"Chromium";v="147.0.7727.102"'
        ),
    },
    # ---- Chrome 136 on Windows ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    },
    # ---- Chrome 135 on Windows ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="135", "Google Chrome";v="135", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"15.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    },
    # ---- Chrome 136 on macOS ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-ch-ua-platform-version": '"15.3.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    },
    # ---- Chrome 134 on macOS ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="134", "Google Chrome";v="134", "Not-A.Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-ch-ua-platform-version": '"14.7.0"',
        "sec-ch-ua-arch": '"arm"',
        "sec-ch-ua-bitness": '"64"',
    },
    # ---- Chrome 136 on Linux ----
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "sec-ch-ua-platform-version": '"6.8.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    },
    # ---- Edge 136 on Windows ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
        ),
        "sec-ch-ua": '"Chromium";v="136", "Microsoft Edge";v="136", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    },
    # ---- Edge 134 on Windows ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
        ),
        "sec-ch-ua": '"Chromium";v="134", "Microsoft Edge";v="134", "Not-A.Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"15.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    },
    # ---- Chrome on Android (Pixel 9) ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua-platform-version": '"15.0.0"',
    },
    # ---- Chrome on Android (Samsung S24) ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Mobile Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="135", "Google Chrome";v="135", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua-platform-version": '"14.0.0"',
    },
    # ---- Firefox on Windows (no sec-ch-ua — Firefox doesn't send them) ----
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    },
    # ---- Firefox on macOS ----
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:138.0) Gecko/20100101 Firefox/138.0",
    },
    # ---- Firefox on Linux ----
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) Gecko/20100101 Firefox/138.0",
    },
    # ---- Safari on macOS (no sec-ch-ua — Safari doesn't send them) ----
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.2 Safari/605.1.15"
        ),
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.1 Safari/605.1.15"
        ),
    },
)

# Legacy flat list for backward-compat imports
_USER_AGENTS: tuple[str, ...] = tuple(p["User-Agent"] for p in _BROWSER_PROFILES)

# Accept-Language variants to rotate — looks like different locales
_ACCEPT_LANGUAGES: tuple[str, ...] = (
    "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en-US;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,fr;q=0.8",
    "en,en-US;q=0.9",
    "en-US,en;q=0.9,de;q=0.8",
    "en-US,en;q=0.8",
    "en-US,en;q=0.9,ja;q=0.8",
    "en-IN,en;q=0.9",
)


def _pick_user_agent() -> str:
    return random.choice(_USER_AGENTS)  # nosec B311


def _pick_accept_lang() -> str:
    return random.choice(_ACCEPT_LANGUAGES)  # nosec B311


def _pick_browser_profile() -> dict[str, str]:
    """Return a full set of coherent browser headers for one request."""
    profile = dict(random.choice(_BROWSER_PROFILES))  # nosec B311
    # Always add common headers
    profile["Accept-Language"] = _pick_accept_lang()
    profile["Accept-Encoding"] = "gzip, deflate, br, zstd"
    profile.setdefault("Sec-Fetch-Dest", "document")
    profile.setdefault("Sec-Fetch-Mode", "navigate")
    profile.setdefault("Sec-Fetch-Site", "none")
    profile.setdefault("Sec-Fetch-User", "?1")
    profile["Upgrade-Insecure-Requests"] = "1"
    profile["Priority"] = "u=0, i"
    return profile


# Default player-client ordering.
#
# Empirically (tested 2026-04 on cloud IPs that
# ``youtube-transcript-api`` gets IP-blocked on after <30 requests):
#
# * ``android`` and ``android_vr`` are the ONLY clients that reliably
#   return caption URLs from an IP YouTube is actively throttling.
#   They don't need PO tokens and have independent rate-limit pools.
# * ``tv_simply`` and ``tv_embedded`` work on fresh IPs and act as
#   secondary fallbacks.
# * ``web``/``mweb``/``ios``/``web_embedded`` need PO tokens or cookies
#   on cloud IPs. We still try them last so cookie-enabled users benefit.
DEFAULT_PLAYER_CLIENTS: tuple[str, ...] = (
    "android",
    "android_vr",
    "tv_simply",
    "tv_embedded",
    "mweb",
    "web",
    "ios",
)

# Captions formats we know how to parse, in preference order.
_PREFERRED_EXTS: tuple[str, ...] = ("json3", "srv3", "srv2", "srv1", "vtt", "ttml")

# Transient-error substrings we will retry on.
_TRANSIENT_MARKERS: tuple[str, ...] = (
    "HTTP Error 429",  # rate-limit
    "HTTP Error 500",
    "HTTP Error 502",
    "HTTP Error 503",
    "HTTP Error 504",
    "timed out",
    "Connection reset",
    "ConnectionResetError",
    "Temporary failure",
    "Unable to download",
    "Got error",
)

# Markers that tell us we hit YouTube's anti-bot heuristics. We'll slow the
# whole worker pool down when this happens, but still keep trying — yt-dlp
# cycles through clients that each have independent quotas.
_ANTI_BOT_MARKERS: tuple[str, ...] = (
    "Sign in to confirm",
    "confirm you're not a bot",
    "player response",
    "This video is not available",  # sometimes geo-lock cover
    "Requested format is not available",
)

# Permanent (do-not-retry) markers.
_PERMANENT_MARKERS: tuple[str, ...] = (
    "Private video",
    "Video unavailable",
    "This live stream recording is not available",
    "members-only",
    "has been removed",
    "removed by the uploader",
    "terminated",
    "copyright",
    "age-restricted",  # without cookies we can't pass these
    "TranscriptsDisabled",
    "NoTranscriptFound",
    "no captions available",
    "no captions in preferred",
    "no preferred-language captions",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_error(msg: str) -> str:
    """Return 'permanent' | 'transient' | 'antibot' | 'unknown'."""
    low = msg.lower()
    for marker in _PERMANENT_MARKERS:
        if marker.lower() in low:
            return "permanent"
    for marker in _ANTI_BOT_MARKERS:
        if marker.lower() in low:
            return "antibot"
    for marker in _TRANSIENT_MARKERS:
        if marker.lower() in low:
            return "transient"
    return "unknown"


def _http_get(url: str, timeout: float = 30.0, ua: str | None = None) -> bytes:
    """Minimal urllib GET with a rotating browser profile and transparent gzip support."""
    profile = _pick_browser_profile()
    if ua:
        profile["User-Agent"] = ua
    headers = {
        "User-Agent": profile["User-Agent"],
        "Accept-Language": profile["Accept-Language"],
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
    }
    # Add sec-ch-ua Client Hints if present
    for key in ("sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"):
        if key in profile:
            headers[key] = profile[key]
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        raw = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        try:
            return gzip.decompress(raw)
        except OSError:
            return cast(bytes, raw)
    if encoding == "deflate":
        try:
            return decompress(raw)
        except Exception:
            try:
                return decompress(raw, -MAX_WBITS)
            except Exception as exc:
                logger.debug("deflate caption decode failed: %s", exc)
                return cast(bytes, raw)
    return cast(bytes, raw)


# ---------------------------------------------------------------------------
# Subtitle parsers — pure functions, each returns list[TranscriptEntry]
# ---------------------------------------------------------------------------


def _parse_json3(data: bytes) -> list[TranscriptEntry]:
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    out: list[TranscriptEntry] = []
    for ev in payload.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        start = float(ev.get("tStartMs", 0)) / 1000.0
        dur = float(ev.get("dDurationMs", 0)) / 1000.0
        out.append(TranscriptEntry(text=text, start=max(start, 0.0), duration=max(dur, 0.0)))
    return out


_SRV_CUE_RE = re.compile(
    r'<text[^>]*start="(?P<start>[0-9.]+)"[^>]*(?:dur="(?P<dur>[0-9.]+)")?[^>]*>'
    r"(?P<text>.*?)</text>",
    re.DOTALL,
)


def _html_unescape(raw: str) -> str:
    import html as _html

    return _html.unescape(raw)


def _parse_xml_captions(data: bytes) -> list[TranscriptEntry]:
    """Parse srv1/srv2/srv3/ttml captions."""
    text = data.decode("utf-8", errors="replace")
    out: list[TranscriptEntry] = []
    # Try with ElementTree first (handles both ttml and srv)
    try:
        root = ElementTree.fromstring(text)
        # TTML: <p begin="..." dur="...">text</p>
        ns = {"tt": "http://www.w3.org/ns/ttml"}
        ps = root.findall(".//tt:p", ns) or root.findall(".//p")
        if ps:
            for p in ps:
                start_attr = p.attrib.get("begin") or p.attrib.get("start") or "0"
                dur_attr = p.attrib.get("dur") or "0"
                txt = "".join(p.itertext()).strip()
                if not txt:
                    continue
                out.append(
                    TranscriptEntry(
                        text=txt,
                        start=_ttml_time(start_attr),
                        duration=_ttml_time(dur_attr),
                    )
                )
            return out
        # srv*: <text start="..." dur="...">text</text>
        for t in root.iter("text"):
            start = float(t.attrib.get("start", 0) or 0)
            dur = float(t.attrib.get("dur", 0) or 0)
            txt = _html_unescape((t.text or "").strip())
            if not txt:
                continue
            out.append(TranscriptEntry(text=txt, start=max(start, 0.0), duration=max(dur, 0.0)))
    except ElementTree.ParseError:
        # Fallback to regex for malformed XML
        for m in _SRV_CUE_RE.finditer(text):
            start = float(m.group("start") or 0)
            dur = float(m.group("dur") or 0)
            txt = _html_unescape(re.sub(r"<[^>]+>", "", m.group("text") or "").strip())
            if not txt:
                continue
            out.append(TranscriptEntry(text=txt, start=max(start, 0.0), duration=max(dur, 0.0)))
    return out


def _ttml_time(value: str) -> float:
    """Parse TTML time expressions: '12.345s' or 'HH:MM:SS.mmm' or seconds."""
    v = value.strip()
    if not v:
        return 0.0
    if v.endswith("s"):
        try:
            return float(v[:-1])
        except ValueError:
            return 0.0
    if ":" in v:
        parts = v.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return 0.0
        total = 0.0
        for n in nums:
            total = total * 60 + n
        return total
    try:
        return float(v)
    except ValueError:
        return 0.0


_VTT_TS_RE = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{3})"
    r"\s*-->\s*"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2})[.,](?P<ms2>\d{3})"
)


def _parse_vtt(data: bytes) -> list[TranscriptEntry]:
    text = data.decode("utf-8", errors="replace")
    # Strip VTT tag markup like <00:00:01.200><c> some text </c>
    buf: list[TranscriptEntry] = []
    lines = text.splitlines()
    i = 0
    # Skip header lines until the first cue
    while i < len(lines):
        m = _VTT_TS_RE.search(lines[i])
        if m:
            start = (
                int(m["h"]) * 3600 + int(m["m"]) * 60 + int(m["s"]) + int(m["ms"]) / 1000.0
            )
            end = (
                int(m["h2"]) * 3600 + int(m["m2"]) * 60 + int(m["s2"]) + int(m["ms2"]) / 1000.0
            )
            i += 1
            cue_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                cue_lines.append(lines[i])
                i += 1
            cue_text = " ".join(cue_lines)
            # Remove timestamps and HTML-like tags
            cue_text = re.sub(r"<[^>]+>", "", cue_text)
            cue_text = re.sub(r"\s+", " ", cue_text).strip()
            if cue_text:
                buf.append(
                    TranscriptEntry(
                        text=_html_unescape(cue_text),
                        start=max(start, 0.0),
                        duration=max(end - start, 0.0),
                    )
                )
        i += 1
    # VTT auto-captions duplicate lines across overlapping cues; dedupe adjacent
    deduped: list[TranscriptEntry] = []
    prev = ""
    for e in buf:
        if e.text == prev:
            continue
        deduped.append(e)
        prev = e.text
    return deduped


def parse_subtitle(data: bytes, ext: str) -> list[TranscriptEntry]:
    """Dispatch to the right parser based on the subtitle extension."""
    ext = (ext or "").lower()
    if ext == "json3":
        return _parse_json3(data)
    if ext in {"srv3", "srv2", "srv1", "xml", "ttml"}:
        return _parse_xml_captions(data)
    if ext in {"vtt", "webvtt"}:
        return _parse_vtt(data)
    # Last resort: try each parser
    for p in (_parse_json3, _parse_xml_captions, _parse_vtt):
        try:
            entries = p(data)
            if entries:
                return entries
        except Exception as exc:
            logger.debug("subtitle parser %s failed: %s", p.__name__, exc)
    return []


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------


class YtDlpTranscriptExtractor:
    """Drop-in replacement for ``TranscriptExtractor`` using yt-dlp."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        player_clients: Iterable[str] | None = None,
    ) -> None:
        self.config = config or Config()
        self.player_clients: tuple[str, ...] = (
            tuple(player_clients) if player_clients else DEFAULT_PLAYER_CLIENTS
        )
        # Adaptive backoff state — shared across all workers via asyncio lock.
        self._throttle_backoff: float = 0.0  # seconds to sleep before next call
        self._throttle_lock: asyncio.Lock | None = None  # created lazily in async code
        self._consecutive_throttles: int = 0

    # ---------- yt-dlp options ----------

    def _build_ydl_opts(self, *, client_override: list[str] | None = None) -> dict[str, Any]:
        cfg = self.config
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _SilentLogger(),
            "skip_download": True,
            "writesubtitles": False,
            "writeautomaticsub": False,
            "subtitleslangs": cfg.languages,
            "extractor_retries": 3,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": cfg.request_timeout,
            # ``ignore_no_formats_error`` lets us still read subtitle URLs
            # even if the chosen player client can't see downloadable formats
            # (e.g. ``web_safari`` and ``mweb`` on flagged IPs).
            "ignore_no_formats_error": True,
            "http_headers": {
                "User-Agent": _pick_user_agent(),
                "Accept-Language": "en-US,en;q=0.9",
            },
            "extractor_args": {
                "youtube": {
                    "player_client": list(client_override or self.player_clients),
                    # Skip HLS/DASH — we only need subtitle URLs. We do NOT
                    # skip ``webpage``/``configs`` any more because that made
                    # certain clients unable to fetch the caption list.
                    "skip": ["hls", "dash", "translated_subs"],
                }
            },
        }
        if cfg.proxy:
            opts["proxy"] = cfg.proxy
        if cfg.cookies_file:
            opts["cookiefile"] = str(cfg.cookies_file)
        if cfg.user_agent:
            opts["http_headers"]["User-Agent"] = cfg.user_agent
        return opts

    # ---------- core single-video fetch ----------

    def fetch_one(self, meta: VideoMetadata) -> TranscriptResult:
        """Sync fetch with per-client fallback.

        Strategy: walk the client list one-at-a-time instead of passing
        them all to a single ``extract_info`` call. Single-client calls
        terminate as soon as they fail, so we waste no time waiting for
        yt-dlp to re-try every client internally with all its own retries.
        The *first* client that returns caption URLs wins.
        """
        url = meta.url or f"https://www.youtube.com/watch?v={meta.video_id}"
        last_error: str = ""
        merged_meta = meta

        for client in self.player_clients:
            try:
                with YoutubeDL(self._build_ydl_opts(client_override=[client])) as ydl:
                    info = ydl.extract_info(url, download=False)
            except (DownloadError, ExtractorError) as exc:
                last_error = str(exc)
                category = _classify_error(last_error)
                logger.debug(
                    "client=%s failed for %s (%s): %s",
                    client,
                    meta.video_id,
                    category,
                    last_error[:200],
                )
                if category == "permanent":
                    return TranscriptResult(
                        metadata=merged_meta, success=False, error=last_error
                    )
                # antibot / transient / unknown — try next client
                continue
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.debug(
                    "client=%s unexpected for %s: %s",
                    client,
                    meta.video_id,
                    last_error[:200],
                )
                continue

            if not info:
                last_error = last_error or "empty info"
                continue

            merged_meta = self._merge_metadata(merged_meta, info)
            result = self._build_result_from_info(merged_meta, info)
            if result.success:
                return result
            # This client returned no usable subs — note and keep trying
            last_error = result.error or last_error
            logger.debug(
                "client=%s no usable subs for %s: %s",
                client,
                meta.video_id,
                last_error[:200],
            )

        return TranscriptResult(
            metadata=merged_meta,
            success=False,
            error=last_error or "no captions available via any client",
        )

    # ---------- metadata merge ----------

    @staticmethod
    def _merge_metadata(meta: VideoMetadata, info: dict[str, Any]) -> VideoMetadata:
        return meta.model_copy(
            update={
                "title": meta.title or str(info.get("title") or ""),
                "channel": meta.channel or str(info.get("channel") or info.get("uploader") or ""),
                "channel_id": meta.channel_id
                or str(info.get("channel_id") or info.get("uploader_id") or ""),
                "duration_seconds": meta.duration_seconds or info.get("duration"),
                "view_count": meta.view_count or info.get("view_count"),
                "upload_date": meta.upload_date or info.get("upload_date"),
                "url": meta.url or f"https://www.youtube.com/watch?v={meta.video_id}",
            }
        )

    # ---------- caption selection ----------

    def _build_result_from_info(
        self, meta: VideoMetadata, info: dict[str, Any]
    ) -> TranscriptResult:
        subs = info.get("subtitles") or {}
        auto = info.get("automatic_captions") or {}

        # 1) Preferred: manually-created in a preferred language
        picked = self._pick_track(subs, self.config.languages)
        is_generated = False

        # 2) Fall back to auto-captions if allowed
        if picked is None and self.config.allow_generated:
            picked = self._pick_track(auto, self.config.languages)
            is_generated = picked is not None

        # 3) Last resort: translation. yt-dlp exposes translated variants
        # under the same dict with keys like 'en-xx' — try any English-ish key
        if picked is None and self.config.allow_translation:
            picked = self._pick_any_english(subs) or self._pick_any_english(auto)
            is_generated = picked is not None and picked[0] is auto

        if picked is None:
            # Absolutely nothing — list what's available for debugging
            langs = sorted(set(list(subs.keys()) + list(auto.keys())))[:20]
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"No captions in preferred languages. Available: {langs}",
            )

        _track_pool, lang, track = picked
        try:
            data = _http_get(track["url"], timeout=self.config.request_timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"caption download failed: {type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"caption download failed: {type(exc).__name__}: {exc}",
            )

        entries = parse_subtitle(data, track.get("ext") or "")
        if not entries:
            # Empty transcript file — consider that a soft failure so caller can retry
            return TranscriptResult(
                metadata=meta,
                success=False,
                error=f"empty captions payload (lang={lang}, ext={track.get('ext')})",
            )

        return TranscriptResult(
            metadata=meta,
            entries=entries,
            language=lang,
            is_generated=is_generated,
            success=True,
        )

    @staticmethod
    def _pick_track(
        pool: dict[str, list[dict[str, Any]]], langs: list[str]
    ) -> tuple[dict[str, list[dict[str, Any]]], str, dict[str, Any]] | None:
        if not pool:
            return None
        # Direct match first
        for lang in langs:
            if lang in pool:
                track = YtDlpTranscriptExtractor._best_format(pool[lang])
                if track:
                    return pool, lang, track
        # Prefix match: "en" should also match "en-US", "en-GB", etc.
        for lang in langs:
            for key in pool:
                if key.startswith(lang):
                    track = YtDlpTranscriptExtractor._best_format(pool[key])
                    if track:
                        return pool, key, track
        return None

    @staticmethod
    def _pick_any_english(
        pool: dict[str, list[dict[str, Any]]],
    ) -> tuple[dict[str, list[dict[str, Any]]], str, dict[str, Any]] | None:
        for key, tracks in pool.items():
            low = key.lower()
            if low.startswith("en") or low.endswith("-en") or "-en-" in low:
                track = YtDlpTranscriptExtractor._best_format(tracks)
                if track:
                    return pool, key, track
        return None

    @staticmethod
    def _best_format(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not tracks:
            return None
        by_ext = {t.get("ext"): t for t in tracks if t.get("url")}
        for ext in _PREFERRED_EXTS:
            if ext in by_ext:
                return by_ext[ext]
        # Fall back to the first track with a URL
        for t in tracks:
            if t.get("url"):
                return t
        return None

    # ---------- async + adaptive throttle ----------

    async def fetch_one_async(self, meta: VideoMetadata) -> TranscriptResult:
        if self._throttle_lock is None:
            self._throttle_lock = asyncio.Lock()

        # Honour a cooperative sleep if prior workers hit throttling
        async with self._throttle_lock:
            delay = self._throttle_backoff

        if delay > 0:
            await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))  # nosec B311

        # Per-request micro-jitter to avoid synchronized hammering.
        await asyncio.sleep(0.05 + 0.25 * random.random())  # nosec B311

        attempts = max(self.config.max_retries + 1, 1)
        result: TranscriptResult | None = None
        for attempt in range(attempts):
            result = await asyncio.to_thread(self.fetch_one, meta)
            if result.success:
                await self._note_success()
                return result

            category = _classify_error(result.error or "")
            if category == "permanent":
                return result
            if category == "antibot":
                await self._note_throttle()

            if attempt < attempts - 1:
                # Exponential backoff with jitter
                base = self.config.retry_initial_delay * (2**attempt)
                backoff = min(base, self.config.retry_max_delay)
                backoff *= 0.7 + 0.6 * random.random()  # nosec B311
                logger.debug(
                    "retry %d for %s after %.2fs (reason=%s)",
                    attempt + 1,
                    meta.video_id,
                    backoff,
                    category,
                )
                await asyncio.sleep(backoff)
        return result or TranscriptResult(metadata=meta, success=False, error="no attempts")

    async def _note_success(self) -> None:
        async with self._lock():
            self._consecutive_throttles = 0
            # Drain the cooperative backoff gradually (don't slam it to zero
            # or we'll just get throttled again immediately).
            self._throttle_backoff = max(0.0, self._throttle_backoff * 0.7 - 0.25)

    async def _note_throttle(self) -> None:
        async with self._lock():
            self._consecutive_throttles += 1
            new = max(self._throttle_backoff * 1.3, 1.0) + self._consecutive_throttles * 0.5
            self._throttle_backoff = min(new, 15.0)
            if self._consecutive_throttles % 5 == 1:
                logger.info(
                    "⚠ ytdlp-backend throttle — next worker waits %.1fs (strike %d)",
                    self._throttle_backoff,
                    self._consecutive_throttles,
                )

    def _lock(self) -> asyncio.Lock:
        if self._throttle_lock is None:
            self._throttle_lock = asyncio.Lock()
        return self._throttle_lock

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
