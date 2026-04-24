"""Tests for watch_extractor.py — covers helpers, track picking, async, and batch."""

from __future__ import annotations

import asyncio
import json
import urllib.error
from typing import Any
from zlib import compress

import pytest

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptResult, VideoMetadata
from yt_transcript_pro.watch_extractor import (
    WatchPageTranscriptExtractor,
    _default_cookies,
    _extract_player_response,
    _open_gzip,
)

# ---------------------------------------------------------------------------
# _open_gzip
# ---------------------------------------------------------------------------


class TestOpenGzip:
    def test_gzip(self) -> None:
        import gzip

        raw = gzip.compress(b"hello")
        assert _open_gzip(raw, "gzip") == b"hello"

    def test_gzip_bad_data_returns_raw(self) -> None:
        assert _open_gzip(b"not-gzip", "gzip") == b"not-gzip"

    def test_deflate(self) -> None:
        raw = compress(b"world")
        assert _open_gzip(raw, "deflate") == b"world"

    def test_deflate_bad_data_returns_raw(self) -> None:
        assert _open_gzip(b"bad", "deflate") == b"bad"

    def test_no_encoding(self) -> None:
        assert _open_gzip(b"plain", "") == b"plain"

    def test_none_encoding(self) -> None:
        assert _open_gzip(b"plain", "") == b"plain"


# ---------------------------------------------------------------------------
# _default_cookies
# ---------------------------------------------------------------------------


def test_default_cookies() -> None:
    cookies = _default_cookies()
    assert "CONSENT" in cookies
    assert "SOCS" in cookies
    assert "PREF" in cookies


# ---------------------------------------------------------------------------
# _extract_player_response fallback path
# ---------------------------------------------------------------------------


def test_extract_player_response_fallback_json_escaped() -> None:
    """The second regex pattern matches JSON-escaped playerResponse."""
    inner = json.dumps({"videoDetails": {"title": "Fallback"}})
    escaped = json.dumps(inner)[1:-1]  # Remove outer quotes
    html = f'"playerResponse":"{escaped}"'
    pr = _extract_player_response(html)
    assert pr is not None
    assert pr["videoDetails"]["title"] == "Fallback"


def test_extract_player_response_fallback_bad_json() -> None:
    html = '"playerResponse":"not valid json at all"'
    assert _extract_player_response(html) is None


def test_extract_player_response_inline_bad_json() -> None:
    html = "ytInitialPlayerResponse = {invalid json};var x=1;"
    assert _extract_player_response(html) is None


# ---------------------------------------------------------------------------
# WatchPageTranscriptExtractor._pick_track
# ---------------------------------------------------------------------------


class TestPickTrack:
    def _ext(self, config: Config | None = None) -> WatchPageTranscriptExtractor:
        return WatchPageTranscriptExtractor(config or Config())

    def test_manual_exact_match(self) -> None:
        tracks = [{"languageCode": "en", "kind": "manual", "baseUrl": "http://a"}]
        result = self._ext()._pick_track(tracks)
        assert result is not None
        lang, _track, is_gen = result
        assert lang == "en"
        assert is_gen is False

    def test_asr_exact_match(self) -> None:
        tracks = [{"languageCode": "en", "kind": "asr", "baseUrl": "http://a"}]
        result = self._ext()._pick_track(tracks)
        assert result is not None
        _lang, _track, is_gen = result
        assert is_gen is True

    def test_asr_not_allowed(self) -> None:
        # Use a non-English track so the English fallback (step 4) doesn't trigger
        tracks = [{"languageCode": "ja", "kind": "asr", "baseUrl": "http://a"}]
        cfg = Config(allow_generated=False, allow_translation=False, languages=["fr"])
        result = self._ext(cfg)._pick_track(tracks)
        assert result is None

    def test_prefix_match(self) -> None:
        tracks = [{"languageCode": "en-US", "kind": "manual", "baseUrl": "http://a"}]
        result = self._ext()._pick_track(tracks)
        assert result is not None
        code, _, _ = result
        assert code == "en-US"

    def test_english_fallback(self) -> None:
        tracks = [{"languageCode": "en-AU", "kind": "asr", "baseUrl": "http://a"}]
        cfg = Config(languages=["fr"], allow_generated=False, allow_translation=False)
        result = self._ext(cfg)._pick_track(tracks)
        assert result is not None
        code, _, _ = result
        assert code.startswith("en")

    def test_translatable_fallback(self) -> None:
        tracks = [
            {"languageCode": "ja", "kind": "manual", "baseUrl": "http://a?x=1", "isTranslatable": True}
        ]
        cfg = Config(languages=["en"], allow_generated=True, allow_translation=True)
        ext = self._ext(cfg)
        result = ext._pick_track(tracks)
        assert result is not None
        lang, track, is_gen = result
        assert "translated" in lang
        assert "tlang=en" in track["baseUrl"]
        assert is_gen is True

    def test_no_match(self) -> None:
        tracks = [{"languageCode": "ja", "kind": "manual", "baseUrl": "http://a"}]
        cfg = Config(languages=["fr"], allow_generated=False, allow_translation=False)
        result = self._ext(cfg)._pick_track(tracks)
        assert result is None


# ---------------------------------------------------------------------------
# fetch_one error paths
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_fetch_one_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    def raise_http(url: str, **kwargs: Any) -> bytes:
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(wem, "_http_get", raise_http)
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "403" in (res.error or "")


def test_fetch_one_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    def raise_exc(url: str, **kwargs: Any) -> bytes:
        raise RuntimeError("boom")

    monkeypatch.setattr(wem, "_http_get", raise_exc)
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "RuntimeError" in (res.error or "")


def test_fetch_one_no_preferred_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "ja", "kind": "manual", "baseUrl": "http://x"},
                ]
            }
        },
    }
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    monkeypatch.setattr(wem, "_http_get", lambda *a, **k: html)

    cfg = Config(languages=["fr"], allow_generated=False, allow_translation=False)
    ext = WatchPageTranscriptExtractor(cfg)
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "no preferred-language" in (res.error or "").lower()


def test_fetch_one_caption_download_fails(monkeypatch: pytest.MonkeyPatch) -> None:
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
    call_count = {"n": 0}

    def fake_get(url: str, **kwargs: Any) -> bytes:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return html
        raise RuntimeError("download fail")

    monkeypatch.setattr(wem, "_http_get", fake_get)
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "caption download failed" in (res.error or "")


def test_fetch_one_empty_captions(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr", "baseUrl": "http://x?a=1"},
                ]
            }
        },
    }
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    call_count = {"n": 0}

    def fake_get(url: str, **kwargs: Any) -> bytes:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return html
        # Return empty json3
        return b'{"events": []}'

    monkeypatch.setattr(wem, "_http_get", fake_get)
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "empty" in (res.error or "").lower()


def test_fetch_one_login_required(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "LOGIN_REQUIRED", "reason": "Sign in"},
        "videoDetails": {},
        "captions": {},
    }
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    monkeypatch.setattr(wem, "_http_get", lambda *a, **k: html)
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "LOGIN_REQUIRED" in (res.error or "")


# ---------------------------------------------------------------------------
# Async fetch_one
# ---------------------------------------------------------------------------


def test_fetch_one_async_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"title": "T", "author": "A"},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr", "baseUrl": "http://x?a=1"},
                ]
            }
        },
    }
    html = f"ytInitialPlayerResponse = {json.dumps(pr)};var x=1;".encode()
    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "yo"}]}]}).encode()

    def fake_get(url: str, **kwargs: Any) -> bytes:
        return html if "/watch" in url else cap

    monkeypatch.setattr(wem, "_http_get", fake_get)
    ext = WatchPageTranscriptExtractor(Config(max_retries=0))
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert res.success


def test_fetch_one_async_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    monkeypatch.setattr(
        wem, "_http_get",
        lambda *a, **k: b"<html>confirm you're not a bot</html>",
    )
    ext = WatchPageTranscriptExtractor(Config(max_retries=1, retry_initial_delay=0.01, retry_max_delay=0.02))
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success
    # Should have increased throttle
    assert ext._consecutive_throttles > 0


def test_fetch_one_async_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ext = WatchPageTranscriptExtractor(Config(max_retries=3))

    def fake_fetch_one(meta: VideoMetadata) -> TranscriptResult:
        return TranscriptResult(
            metadata=meta,
            success=False,
            error="NoTranscriptFound: no captions",
        )

    monkeypatch.setattr(ext, "fetch_one", fake_fetch_one)
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success
    assert res.error == "NoTranscriptFound: no captions"


def test_note_success_initializes_lock() -> None:
    ext = WatchPageTranscriptExtractor(Config())
    assert ext._throttle_lock is None
    asyncio.run(ext._note_success())
    assert ext._throttle_lock is not None


# ---------------------------------------------------------------------------
# fetch_many batch
# ---------------------------------------------------------------------------


def test_fetch_many_empty() -> None:
    ext = WatchPageTranscriptExtractor(Config())
    results = asyncio.run(ext.fetch_many([]))
    assert results == []


def test_fetch_many_with_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {},
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

    progress_calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int, res: TranscriptResult) -> None:
        progress_calls.append((done, total))

    ext = WatchPageTranscriptExtractor(Config(concurrency=2, max_retries=0))
    vids = [VideoMetadata(video_id=f"aaaaaaaaa{i:02d}") for i in range(3)]
    results = asyncio.run(ext.fetch_many(vids, progress=on_progress))
    assert len(results) == 3
    assert len(progress_calls) == 3


def test_fetch_many_progress_callback_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    pr = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {},
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

    def bad_progress(done: int, total: int, res: TranscriptResult) -> None:
        raise ValueError("callback crash")

    ext = WatchPageTranscriptExtractor(Config(concurrency=1, max_retries=0))
    vids = [VideoMetadata(video_id="abcdefghijk")]
    # Should not raise despite callback error
    results = asyncio.run(ext.fetch_many(vids, progress=bad_progress))
    assert len(results) == 1


# ---------------------------------------------------------------------------
# _add_query
# ---------------------------------------------------------------------------


def test_add_query() -> None:
    ext = WatchPageTranscriptExtractor()
    url = ext._add_query("http://example.com/cap?a=1", {"fmt": "json3"})
    assert "fmt=json3" in url
    assert "a=1" in url


def test_add_query_makes_youtube_relative_urls_absolute() -> None:
    ext = WatchPageTranscriptExtractor()
    url = ext._add_query("/api/timedtext?v=abcdefghijk", {"fmt": "json3"})
    assert url.startswith("https://www.youtube.com/api/timedtext?")
    assert "fmt=json3" in url


def test_add_query_makes_protocol_relative_urls_https() -> None:
    ext = WatchPageTranscriptExtractor()
    url = ext._add_query("//www.youtube.com/api/timedtext?v=abc", {"fmt": "json3"})
    assert url.startswith("https://www.youtube.com/api/timedtext?")


def test_http_get_with_user_agent_override(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.watch_extractor as wem

    class FakeResponse:
        def __init__(self) -> None:
            self.headers = {"Content-Encoding": ""}

        def read(self) -> bytes:
            return b"ok"

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        wem,
        "_pick_browser_profile",
        lambda: {"User-Agent": "default", "Accept-Language": "en"},
    )
    monkeypatch.setattr(
        wem.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )

    assert (
        wem._http_get(
            "https://www.youtube.com/watch?v=abcdefghijk",
            timeout=1,
            ua="custom",
        )
        == b"ok"
    )
    assert (
        wem._http_get("https://www.youtube.com/watch?v=abcdefghijk", timeout=1, ua="")
        == b"ok"
    )
