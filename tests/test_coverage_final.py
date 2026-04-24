"""Final coverage tests targeting remaining uncovered lines."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata

# ---------------------------------------------------------------------------
# auto_extractor line 122: api backend async path
# ---------------------------------------------------------------------------


def test_auto_async_api_backend() -> None:
    """Exercise the `else` (api) branch in fetch_one_async."""
    from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor

    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["api"],
    )

    good = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        entries=[TranscriptEntry(text="ok", start=0.0, duration=1.0)],
        language="en",
        success=True,
    )

    # Patch the api extractor's async method
    async def api_ok(meta: VideoMetadata) -> TranscriptResult:
        return good

    auto._api.fetch_one_async = api_ok  # type: ignore[assignment]

    res = asyncio.run(auto.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert res.success


# ---------------------------------------------------------------------------
# auto_extractor: ytdlp async path (line 120)
# ---------------------------------------------------------------------------


def test_auto_async_ytdlp_backend() -> None:
    """Exercise the `elif backend == 'ytdlp'` branch in fetch_one_async."""
    from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor

    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["ytdlp"],
    )

    good = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        entries=[TranscriptEntry(text="ok", start=0.0, duration=1.0)],
        language="en",
        success=True,
    )

    async def ytdlp_ok(meta: VideoMetadata) -> TranscriptResult:
        return good

    auto._ytdlp.fetch_one_async = ytdlp_ok  # type: ignore[assignment]

    res = asyncio.run(auto.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert res.success


# ---------------------------------------------------------------------------
# watch_extractor: _http_get tested via mocking urllib
# ---------------------------------------------------------------------------


def test_watch_http_get_plain() -> None:
    import yt_transcript_pro.watch_extractor as wem

    fake_resp = MagicMock()
    fake_resp.read.return_value = b"hello"
    fake_resp.headers.get.return_value = ""
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    with patch.object(wem.urllib.request, "urlopen", return_value=fake_resp):
        result = wem._http_get("http://example.com", timeout=5, ua="test")
    assert result == b"hello"


def test_watch_http_get_with_cookies() -> None:
    import yt_transcript_pro.watch_extractor as wem

    fake_resp = MagicMock()
    fake_resp.read.return_value = b"data"
    fake_resp.headers.get.return_value = ""
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    with patch.object(wem.urllib.request, "urlopen", return_value=fake_resp):
        result = wem._http_get("http://example.com", timeout=5, ua="test", cookies={"k": "v"})
    assert result == b"data"


# ---------------------------------------------------------------------------
# ytdlp_extractor: _http_get tested via mocking urllib
# ---------------------------------------------------------------------------


def test_ytdlp_http_get_gzip() -> None:
    import gzip

    import yt_transcript_pro.ytdlp_extractor as yem

    compressed = gzip.compress(b"hello world")
    fake_resp = MagicMock()
    fake_resp.read.return_value = compressed
    fake_resp.headers.get.return_value = "gzip"
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    with patch.object(yem.urllib.request, "urlopen", return_value=fake_resp):
        result = yem._http_get("http://example.com")
    assert result == b"hello world"


def test_ytdlp_http_get_deflate() -> None:
    from zlib import compress

    import yt_transcript_pro.ytdlp_extractor as yem

    compressed = compress(b"hello world")
    fake_resp = MagicMock()
    fake_resp.read.return_value = compressed
    fake_resp.headers.get.return_value = "deflate"
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    with patch.object(yem.urllib.request, "urlopen", return_value=fake_resp):
        result = yem._http_get("http://example.com")
    assert result == b"hello world"


def test_ytdlp_http_get_bad_gzip() -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    fake_resp = MagicMock()
    fake_resp.read.return_value = b"not-gzip"
    fake_resp.headers.get.return_value = "gzip"
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    with patch.object(yem.urllib.request, "urlopen", return_value=fake_resp):
        # Falls through, returns raw
        yem._http_get("http://example.com")


def test_ytdlp_http_get_bad_deflate() -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    fake_resp = MagicMock()
    fake_resp.read.return_value = b"not-deflate"
    fake_resp.headers.get.return_value = "deflate"
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    with patch.object(yem.urllib.request, "urlopen", return_value=fake_resp):
        yem._http_get("http://example.com")


def test_ytdlp_http_get_plain() -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    fake_resp = MagicMock()
    fake_resp.read.return_value = b"plain"
    fake_resp.headers.get.return_value = ""
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    with patch.object(yem.urllib.request, "urlopen", return_value=fake_resp):
        result = yem._http_get("http://example.com")
    assert result == b"plain"


# ---------------------------------------------------------------------------
# watch_extractor: bot challenge with ytInitialPlayerResponse present
# ---------------------------------------------------------------------------


def test_watch_bot_challenge_but_has_player_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bot keywords present but ytInitialPlayerResponse IS also present → not a bot challenge."""
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"title": "T"},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr", "baseUrl": "http://x?a=1"},
                ]
            }
        },
    }
    # HTML has bot keywords BUT also has ytInitialPlayerResponse
    html = f"confirm you're not a bot ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]}).encode()

    def fake_get(url: str, **kwargs: Any) -> bytes:
        return html if "/watch" in url else cap

    monkeypatch.setattr(wem, "_http_get", fake_get)
    from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert res.success


# ---------------------------------------------------------------------------
# watch_extractor: video metadata enrichment paths
# ---------------------------------------------------------------------------


def test_watch_metadata_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that videoDetails with lengthSeconds and viewCount get merged."""
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {
            "title": "My Title",
            "author": "My Author",
            "channelId": "UC_abc",
            "lengthSeconds": "120",
            "viewCount": "5000",
        },
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr", "baseUrl": "http://x?a=1"},
                ]
            }
        },
    }
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "x"}]}]}).encode()

    def fake_get(url: str, **kwargs: Any) -> bytes:
        return html if "/watch" in url else cap

    monkeypatch.setattr(wem, "_http_get", fake_get)
    from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert res.success
    assert res.metadata.title == "My Title"
    assert res.metadata.duration_seconds == 120
    assert res.metadata.view_count == 5000


# ---------------------------------------------------------------------------
# watch_extractor: async fetch_one retry with non-throttle error
# ---------------------------------------------------------------------------


def test_watch_async_retry_non_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    monkeypatch.setattr(
        wem, "_http_get",
        lambda *a, **k: b"<html>some error page</html>",
    )
    from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
    ext = WatchPageTranscriptExtractor(Config(max_retries=1, retry_initial_delay=0.01, retry_max_delay=0.02))
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success
    # Should NOT have increased throttle counter (not a bot/429 error)
    assert ext._consecutive_throttles == 0


# ---------------------------------------------------------------------------
# ytdlp_extractor: _merge_metadata
# ---------------------------------------------------------------------------


def test_merge_metadata() -> None:
    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor

    meta = VideoMetadata(video_id="abcdefghijk")
    info = {
        "title": "T",
        "channel": "C",
        "channel_id": "UC_123",
        "duration": 100,
        "view_count": 999,
        "upload_date": "20260101",
    }
    merged = YtDlpTranscriptExtractor._merge_metadata(meta, info)
    assert merged.title == "T"
    assert merged.channel == "C"
    assert merged.duration_seconds == 100
    assert merged.view_count == 999


# ---------------------------------------------------------------------------
# ytdlp_extractor: fetch_many empty
# ---------------------------------------------------------------------------


def test_ytdlp_fetch_many_empty() -> None:
    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor
    ext = YtDlpTranscriptExtractor(Config())
    results = asyncio.run(ext.fetch_many([]))
    assert results == []


# ---------------------------------------------------------------------------
# TTML empty text line (line 281 - continue)
# ---------------------------------------------------------------------------


def test_ttml_empty_text_element() -> None:
    """TTML with empty <p> elements should be skipped."""
    from yt_transcript_pro.ytdlp_extractor import _parse_xml_captions

    data = (
        b'<?xml version="1.0"?>'
        b'<tt xmlns="http://www.w3.org/ns/ttml">'
        b'<body><div>'
        b'<p begin="1.0s" dur="1.0s"></p>'
        b'<p begin="2.0s" dur="1.0s">content</p>'
        b'</div></body></tt>'
    )
    entries = _parse_xml_captions(data)
    assert len(entries) == 1
    assert entries[0].text == "content"


# ---------------------------------------------------------------------------
# XML regex fallback with empty text (line 305 - continue)
# ---------------------------------------------------------------------------


def test_xml_regex_fallback_empty_text() -> None:
    from yt_transcript_pro.ytdlp_extractor import _parse_xml_captions

    data = b'<text start="1.0" dur="2.0"></text><text start="3.0" dur="1.0">hello</text><unclosed'
    entries = _parse_xml_captions(data)
    assert len(entries) == 1
    assert entries[0].text == "hello"


# ---------------------------------------------------------------------------
# parse_subtitle: parser raises exception in fallback (lines 403-404)
# ---------------------------------------------------------------------------


def test_parse_subtitle_parser_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """When unknown ext and a parser throws, it should continue to the next."""
    import yt_transcript_pro.ytdlp_extractor as yem


    def broken_json3(data: bytes) -> list:
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(yem, "_parse_json3", broken_json3)

    # Data that json3 would fail on (patched to throw), but xml can parse
    data = b'<?xml version="1.0"?><transcript><text start="0.5" dur="1.0">test</text></transcript>'
    entries = yem.parse_subtitle(data, "mystery_format")
    assert len(entries) >= 1
    assert entries[0].text == "test"


# ---------------------------------------------------------------------------
# ytdlp async: throttle delay > 0 (line 683)
# ---------------------------------------------------------------------------


def test_ytdlp_async_with_throttle_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            return {
                "subtitles": {},
                "automatic_captions": {"en": [{"ext": "json3", "url": "http://x"}]},
            }

    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]}).encode()
    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: cap)

    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor
    ext = YtDlpTranscriptExtractor(Config(max_retries=0))
    # Pre-set throttle to trigger the delay > 0 path
    ext._throttle_backoff = 0.01
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert res.success


# ---------------------------------------------------------------------------
# ytdlp async: permanent error (line 698)
# ---------------------------------------------------------------------------


def test_ytdlp_async_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from yt_dlp.utils import DownloadError

    import yt_transcript_pro.ytdlp_extractor as yem

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            raise DownloadError("Private video")

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor
    ext = YtDlpTranscriptExtractor(Config(max_retries=2), player_clients=["android"])
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success
    assert "Private" in (res.error or "")


# ---------------------------------------------------------------------------
# watch_extractor: async throttle delay > 0 (line 311)
# ---------------------------------------------------------------------------


def test_watch_async_with_throttle_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"title": "T"},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr", "baseUrl": "http://x?a=1"},
                ]
            }
        },
    }
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]}).encode()

    def fake_get(url: str, **kwargs: Any) -> bytes:
        return html if "/watch" in url else cap

    monkeypatch.setattr(wem, "_http_get", fake_get)
    from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    # Pre-set throttle to trigger the delay > 0 path
    ext._throttle_backoff = 0.01
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert res.success


# ---------------------------------------------------------------------------
# watch_extractor: prefix match in _pick_track (line 277)
# This requires tracks with no exact match but a prefix match
# where no ASR match and no manual match, only prefix
# ---------------------------------------------------------------------------


def test_watch_pick_track_prefix_only() -> None:
    """No exact match, but prefix match hits line 277."""
    from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor

    # Languages: ["zh"] should not match "en-US" exactly, but "zh-TW" should prefix match
    cfg = Config(languages=["zh"], allow_generated=False, allow_translation=False)
    ext = WatchPageTranscriptExtractor(cfg)
    tracks = [{"languageCode": "zh-TW", "kind": "manual", "baseUrl": "http://a"}]
    result = ext._pick_track(tracks)
    assert result is not None
    code, _, _is_gen = result
    assert code == "zh-TW"


# ---------------------------------------------------------------------------
# ytdlp_extractor: fetch_one with multiple clients, subs in later client
# ---------------------------------------------------------------------------


def test_ytdlp_fetch_one_subs_in_second_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """First client returns info but no subs; second returns subs."""
    import yt_transcript_pro.ytdlp_extractor as yem

    call_idx = {"n": 0}

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            call_idx["n"] += 1
            if call_idx["n"] == 1:
                return {"subtitles": {}, "automatic_captions": {}}
            return {
                "subtitles": {},
                "automatic_captions": {"en": [{"ext": "json3", "url": "http://x"}]},
            }

    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hello"}]}]}).encode()
    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: cap)

    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor
    ext = YtDlpTranscriptExtractor(
        Config(max_retries=0, allow_translation=False),
        player_clients=["android", "web"],
    )
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert res.success
    assert call_idx["n"] == 2


# ---------------------------------------------------------------------------
# cli: non-TTY logging path (line 314, 322)
# ---------------------------------------------------------------------------


def test_cli_non_tty_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Simulate non-TTY output to exercise the non-TTY fallback logging."""
    from typer.testing import CliRunner

    from yt_transcript_pro import cli as cli_module
    from yt_transcript_pro.cli import app

    runner = CliRunner()
    meta = VideoMetadata(video_id="abcdefghijk", url="u")
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: [meta])

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        r = TranscriptResult(
            metadata=meta,
            entries=[TranscriptEntry(text="hi", start=0.0, duration=1.0)],
            language="en",
            success=True,
        )
        if progress:
            progress(1, 1, r)
        return [r]

    monkeypatch.setattr(
        cli_module.TranscriptExtractor, "fetch_many", fake_fetch_many
    )

    # Force non-TTY
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    outdir = tmp_path / "out"
    result = runner.invoke(
        app,
        ["extract", "abcdefghijk", "-o", str(outdir), "--no-resume", "--backend", "api"],
    )
    assert result.exit_code == 0
