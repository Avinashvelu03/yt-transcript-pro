"""Unit tests for the yt-dlp and watch backends.

These don't hit the network — yt-dlp's ``extract_info`` is monkey-patched
to return synthetic responses that exercise every code path.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import VideoMetadata
from yt_transcript_pro.watch_extractor import (
    WatchPageTranscriptExtractor,
    _extract_player_response,
)
from yt_transcript_pro.ytdlp_extractor import (
    _classify_error,
    _parse_json3,
    _parse_vtt,
    _parse_xml_captions,
    YtDlpTranscriptExtractor,
    parse_subtitle,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_json3_basic() -> None:
    payload = {
        "events": [
            {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
            {"tStartMs": 3500, "dDurationMs": 1500, "segs": [{"utf8": "\n"}]},  # ignored
            {"tStartMs": 5000, "dDurationMs": 1000, "segs": [{"utf8": "goodbye"}]},
        ]
    }
    entries = _parse_json3(json.dumps(payload).encode())
    assert [e.text for e in entries] == ["hello world", "goodbye"]
    assert entries[0].start == pytest.approx(1.0)
    assert entries[0].duration == pytest.approx(2.0)


def test_parse_xml_captions_srv() -> None:
    data = b'<?xml version="1.0"?><transcript>' \
           b'<text start="0.5" dur="2.0">&amp;hello</text>' \
           b'<text start="3.0" dur="1.0">world</text></transcript>'
    entries = _parse_xml_captions(data)
    assert len(entries) == 2
    assert entries[0].text == "&hello"
    assert entries[0].start == 0.5


def test_parse_vtt() -> None:
    data = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "<c.colorFFFFFF>hello</c> world\n\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "next cue\n"
    ).encode()
    entries = _parse_vtt(data)
    assert entries[0].text == "hello world"
    assert entries[0].start == 1.0
    assert entries[0].duration == 2.0


def test_parse_subtitle_dispatch_and_fallback() -> None:
    # Dispatch by ext
    payload = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "x"}]}]}).encode()
    assert parse_subtitle(payload, "json3")[0].text == "x"
    # Unknown ext falls back to trying parsers
    entries = parse_subtitle(payload, "mystery")
    assert entries[0].text == "x"


def test_classify_error() -> None:
    assert _classify_error("Private video") == "permanent"
    assert _classify_error("Sign in to confirm you're not a bot") == "antibot"
    assert _classify_error("HTTP Error 429: Too Many") == "transient"
    assert _classify_error("something weird") == "unknown"


# ---------------------------------------------------------------------------
# watch-page response parsing
# ---------------------------------------------------------------------------


def test_extract_player_response_inline() -> None:
    html = (
        "junk; ytInitialPlayerResponse = "
        '{"videoDetails":{"title":"T"},"captions":{}};'
        "var foo = 1;"
    )
    pr = _extract_player_response(html)
    assert pr is not None
    assert pr["videoDetails"]["title"] == "T"


def test_extract_player_response_missing() -> None:
    assert _extract_player_response("<html>no data</html>") is None


# ---------------------------------------------------------------------------
# YtDlpTranscriptExtractor.fetch_one (with monkey-patched yt-dlp)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_info_success() -> dict[str, Any]:
    return {
        "title": "T",
        "channel": "C",
        "channel_id": "UC_123",
        "duration": 100,
        "automatic_captions": {
            "en": [
                {"ext": "json3", "url": "https://example.com/captions.json"},
            ]
        },
        "subtitles": {},
    }


def test_ytdlp_fetch_one_success(monkeypatch: pytest.MonkeyPatch, fake_info_success: dict[str, Any]) -> None:
    """Simulate a client returning captions and a successful download."""
    import yt_transcript_pro.ytdlp_extractor as yem

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            return fake_info_success

    fake_payload = json.dumps({
        "events": [
            {"tStartMs": 0, "dDurationMs": 500, "segs": [{"utf8": "hi"}]}
        ]
    }).encode()
    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: fake_payload)

    ext = YtDlpTranscriptExtractor(Config(concurrency=1, max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert res.success
    assert res.is_generated is True
    assert res.entries[0].text == "hi"
    assert res.metadata.title == "T"


def test_ytdlp_fetch_one_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem
    from yt_dlp.utils import DownloadError

    class FakeYDL:
        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            raise DownloadError("Private video")

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    ext = YtDlpTranscriptExtractor(Config(concurrency=1, max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "Private" in (res.error or "")


def test_ytdlp_fetch_one_all_clients_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem
    from yt_dlp.utils import DownloadError

    class FakeYDL:
        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            raise DownloadError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    ext = YtDlpTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        player_clients=["android", "tv_simply"],
    )
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success


# ---------------------------------------------------------------------------
# Async + batch
# ---------------------------------------------------------------------------


def test_ytdlp_fetch_many(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    fake = {"automatic_captions": {"en": [{"ext": "json3", "url": "x"}]}}

    class FakeYDL:
        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            return fake

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    payload = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "x"}]}]}).encode()
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: payload)

    ext = YtDlpTranscriptExtractor(Config(concurrency=3, max_retries=0))
    vids = [VideoMetadata(video_id=f"aaaaaaaaa{i:02d}") for i in range(4)]
    results = asyncio.run(ext.fetch_many(vids))
    assert len(results) == 4
    assert all(r.success for r in results)


# ---------------------------------------------------------------------------
# WatchPageTranscriptExtractor
# ---------------------------------------------------------------------------


def test_watch_fetch_one_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    player_response = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"title": "T", "author": "A", "channelId": "UC_xx", "lengthSeconds": "42", "viewCount": "99"},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr", "baseUrl": "https://example.com/cap?x=1"},
                ]
            }
        },
    }
    html = f"blah; ytInitialPlayerResponse = {json.dumps(player_response)};var foo=1;".encode()
    cap_payload = json.dumps({
        "events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]
    }).encode()

    calls = []

    def fake_get(url: str, *, timeout: float, ua: str, cookies: Any = None) -> bytes:
        calls.append(url)
        return html if "/watch" in url else cap_payload

    monkeypatch.setattr(wem, "_http_get", fake_get)

    ext = WatchPageTranscriptExtractor(Config(concurrency=1, max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert res.success
    assert res.entries[0].text == "hi"
    assert res.metadata.title == "T"
    assert res.is_generated is True
    # Ensure json3 was forced
    assert "fmt=json3" in calls[1]


def test_watch_fetch_one_bot_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    monkeypatch.setattr(
        wem, "_http_get",
        lambda *a, **k: b"<html>Please confirm you're not a bot</html>",
    )
    ext = WatchPageTranscriptExtractor(Config(concurrency=1, max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "bot" in (res.error or "").lower()


def test_watch_fetch_one_no_captions(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {"playabilityStatus": {"status": "OK"}, "videoDetails": {}, "captions": {}}
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    monkeypatch.setattr(wem, "_http_get", lambda *a, **k: html)
    ext = WatchPageTranscriptExtractor(Config(concurrency=1, max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "no captions" in (res.error or "").lower()


def test_watch_fetch_one_unplayable(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "UNPLAYABLE", "reason": "Private video"},
        "videoDetails": {},
        "captions": {},
    }
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    monkeypatch.setattr(wem, "_http_get", lambda *a, **k: html)
    ext = WatchPageTranscriptExtractor(Config(concurrency=1, max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "UNPLAYABLE" in (res.error or "")


# ---------------------------------------------------------------------------
# AutoTranscriptExtractor cascade
# ---------------------------------------------------------------------------


def test_auto_cascade_stops_at_first_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor
    from yt_transcript_pro.models import TranscriptEntry, TranscriptResult

    good = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        entries=[TranscriptEntry(text="ok", start=0.0, duration=1.0)],
        language="en",
        success=True,
    )
    bad = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        success=False,
        error="transient: 429",
    )

    # Force explicit backend order so the test is insensitive to the default.
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch", "ytdlp", "api"],
    )
    # watch succeeds → ytdlp and api must never be called
    monkeypatch.setattr(auto._watch, "fetch_one", lambda m: good)
    monkeypatch.setattr(auto._ytdlp, "fetch_one", lambda m: (_ for _ in ()).throw(AssertionError("called")))
    monkeypatch.setattr(auto._api, "fetch_one", lambda m: (_ for _ in ()).throw(AssertionError("called")))
    res = auto.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert res.success

    # watch fails → ytdlp is tried; if it succeeds, api is not
    monkeypatch.setattr(auto._watch, "fetch_one", lambda m: bad)
    monkeypatch.setattr(auto._ytdlp, "fetch_one", lambda m: good)
    monkeypatch.setattr(auto._api, "fetch_one", lambda m: (_ for _ in ()).throw(AssertionError("called")))
    res = auto.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert res.success


def test_auto_cascade_stops_on_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor
    from yt_transcript_pro.models import TranscriptResult

    permanent = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        success=False,
        error="Private video: unavailable",
    )
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch", "ytdlp", "api"],
    )
    monkeypatch.setattr(auto._watch, "fetch_one", lambda m: permanent)
    monkeypatch.setattr(auto._ytdlp, "fetch_one", lambda m: (_ for _ in ()).throw(AssertionError("called")))
    monkeypatch.setattr(auto._api, "fetch_one", lambda m: (_ for _ in ()).throw(AssertionError("called")))
    res = auto.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "Private" in (res.error or "")


def test_auto_circuit_breaker_disables_bad_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """After _BREAKER_THRESHOLD consecutive non-permanent failures a
    backend is skipped on subsequent fetches."""
    from yt_transcript_pro import auto_extractor as ae
    from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor
    from yt_transcript_pro.models import TranscriptEntry, TranscriptResult

    good = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        entries=[TranscriptEntry(text="ok", start=0.0, duration=1.0)],
        language="en",
        success=True,
    )
    bad = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        success=False,
        error="transient: bot",
    )

    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch", "ytdlp"],
    )

    watch_calls = {"n": 0}

    def bad_watch(m: VideoMetadata) -> TranscriptResult:
        watch_calls["n"] += 1
        return bad

    monkeypatch.setattr(auto._watch, "fetch_one", bad_watch)
    monkeypatch.setattr(auto._ytdlp, "fetch_one", lambda m: good)

    # Trip the breaker by running _BREAKER_THRESHOLD+ fetches
    for i in range(ae._BREAKER_THRESHOLD + 3):
        res = auto.fetch_one(VideoMetadata(video_id="abcdefghijk"))
        assert res.success  # ytdlp eventually carries the load

    # watch should stop being tried after the breaker opens
    assert watch_calls["n"] == ae._BREAKER_THRESHOLD
