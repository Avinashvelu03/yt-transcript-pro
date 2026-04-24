"""Additional tests for ytdlp_extractor.py — covers remaining uncovered paths."""

from __future__ import annotations

import asyncio
import json
import urllib.error
from typing import Any

import pytest

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptResult, VideoMetadata
from yt_transcript_pro.ytdlp_extractor import (
    YtDlpTranscriptExtractor,
    _parse_json3,
    _parse_vtt,
    _parse_xml_captions,
    _SilentLogger,
    _ttml_time,
    parse_subtitle,
)

# ---------------------------------------------------------------------------
# _SilentLogger
# ---------------------------------------------------------------------------


def test_silent_logger() -> None:
    sl = _SilentLogger()
    sl.debug("test")
    sl.info("test")
    sl.warning("test")
    sl.error("test")


# ---------------------------------------------------------------------------
# _ttml_time
# ---------------------------------------------------------------------------


class TestTtmlTime:
    def test_seconds_suffix(self) -> None:
        assert _ttml_time("12.345s") == pytest.approx(12.345)

    def test_seconds_suffix_bad(self) -> None:
        assert _ttml_time("bads") == 0.0

    def test_hms(self) -> None:
        assert _ttml_time("01:02:03.5") == pytest.approx(3723.5)

    def test_hms_bad(self) -> None:
        assert _ttml_time("a:b:c") == 0.0

    def test_plain_float(self) -> None:
        assert _ttml_time("42.5") == pytest.approx(42.5)

    def test_plain_float_bad(self) -> None:
        assert _ttml_time("not_a_number") == 0.0

    def test_empty(self) -> None:
        assert _ttml_time("") == 0.0


# ---------------------------------------------------------------------------
# _parse_json3 edge cases
# ---------------------------------------------------------------------------


def test_parse_json3_empty() -> None:
    assert _parse_json3(b"") == []


def test_parse_json3_bad_json() -> None:
    assert _parse_json3(b"not json") == []


def test_parse_json3_not_dict() -> None:
    assert _parse_json3(b"[1,2,3]") == []


def test_parse_json3_no_segs() -> None:
    payload = {"events": [{"tStartMs": 0, "dDurationMs": 1000}]}
    entries = _parse_json3(json.dumps(payload).encode())
    assert entries == []


# ---------------------------------------------------------------------------
# _parse_xml_captions: TTML
# ---------------------------------------------------------------------------


def test_parse_ttml() -> None:
    data = (
        b'<?xml version="1.0"?>'
        b'<tt xmlns="http://www.w3.org/ns/ttml">'
        b'<body><div>'
        b'<p begin="1.5s" dur="2.0s">hello</p>'
        b'<p begin="4.0s" dur="1.0s">world</p>'
        b'</div></body></tt>'
    )
    entries = _parse_xml_captions(data)
    assert len(entries) == 2
    assert entries[0].text == "hello"
    assert entries[0].start == pytest.approx(1.5)


def test_parse_xml_empty_text() -> None:
    data = b'<?xml version="1.0"?><transcript><text start="0" dur="1"></text></transcript>'
    entries = _parse_xml_captions(data)
    assert entries == []


def test_parse_xml_regex_fallback() -> None:
    """Malformed XML falls through to regex parsing."""
    data = b'<text start="1.0" dur="2.0">fallback</text><unclosed'
    entries = _parse_xml_captions(data)
    assert len(entries) == 1
    assert entries[0].text == "fallback"


# ---------------------------------------------------------------------------
# _parse_vtt: dedup
# ---------------------------------------------------------------------------


def test_parse_vtt_dedup() -> None:
    data = (
        b"WEBVTT\n\n"
        b"00:00:01.000 --> 00:00:03.000\n"
        b"same text\n\n"
        b"00:00:03.000 --> 00:00:05.000\n"
        b"same text\n\n"
        b"00:00:05.000 --> 00:00:07.000\n"
        b"different\n"
    )
    entries = _parse_vtt(data)
    assert [e.text for e in entries] == ["same text", "different"]


# ---------------------------------------------------------------------------
# parse_subtitle: unknown ext with parser exception
# ---------------------------------------------------------------------------


def test_parse_subtitle_all_parsers_fail() -> None:
    entries = parse_subtitle(b"\x00\x01\x02", "unknown")
    assert entries == []


def test_parse_subtitle_srv3() -> None:
    data = b'<?xml version="1.0"?><transcript><text start="0.5" dur="2.0">hello</text></transcript>'
    entries = parse_subtitle(data, "srv3")
    assert entries[0].text == "hello"


def test_parse_subtitle_vtt() -> None:
    data = (
        b"WEBVTT\n\n"
        b"00:00:01.000 --> 00:00:03.000\n"
        b"hi\n"
    )
    entries = parse_subtitle(data, "vtt")
    assert entries[0].text == "hi"


# ---------------------------------------------------------------------------
# YtDlpTranscriptExtractor: _pick_track, _pick_any_english, _best_format
# ---------------------------------------------------------------------------


class TestPickTrack:
    def test_direct_match(self) -> None:
        pool = {"en": [{"ext": "json3", "url": "http://x"}]}
        result = YtDlpTranscriptExtractor._pick_track(pool, ["en"])
        assert result is not None
        _, lang, _track = result
        assert lang == "en"

    def test_prefix_match(self) -> None:
        pool = {"en-US": [{"ext": "json3", "url": "http://x"}]}
        result = YtDlpTranscriptExtractor._pick_track(pool, ["en"])
        assert result is not None
        _, lang, _ = result
        assert lang == "en-US"

    def test_no_match(self) -> None:
        pool = {"ja": [{"ext": "json3", "url": "http://x"}]}
        result = YtDlpTranscriptExtractor._pick_track(pool, ["en"])
        assert result is None

    def test_empty_pool(self) -> None:
        assert YtDlpTranscriptExtractor._pick_track({}, ["en"]) is None


class TestPickAnyEnglish:
    def test_found(self) -> None:
        pool = {"en-GB": [{"ext": "json3", "url": "http://x"}]}
        result = YtDlpTranscriptExtractor._pick_any_english(pool)
        assert result is not None

    def test_suffix_en(self) -> None:
        pool = {"xx-en": [{"ext": "json3", "url": "http://x"}]}
        result = YtDlpTranscriptExtractor._pick_any_english(pool)
        assert result is not None

    def test_mid_en(self) -> None:
        pool = {"xx-en-yy": [{"ext": "json3", "url": "http://x"}]}
        result = YtDlpTranscriptExtractor._pick_any_english(pool)
        assert result is not None

    def test_no_english(self) -> None:
        pool = {"ja": [{"ext": "json3", "url": "http://x"}]}
        assert YtDlpTranscriptExtractor._pick_any_english(pool) is None


class TestBestFormat:
    def test_preferred_order(self) -> None:
        tracks = [
            {"ext": "vtt", "url": "a"},
            {"ext": "json3", "url": "b"},
        ]
        result = YtDlpTranscriptExtractor._best_format(tracks)
        assert result is not None
        assert result["ext"] == "json3"

    def test_fallback_first_with_url(self) -> None:
        tracks = [
            {"ext": "mystery", "url": "a"},
        ]
        result = YtDlpTranscriptExtractor._best_format(tracks)
        assert result is not None
        assert result["url"] == "a"

    def test_empty(self) -> None:
        assert YtDlpTranscriptExtractor._best_format([]) is None

    def test_no_url(self) -> None:
        tracks = [{"ext": "json3"}]
        assert YtDlpTranscriptExtractor._best_format(tracks) is None


# ---------------------------------------------------------------------------
# _build_result_from_info: translation fallback
# ---------------------------------------------------------------------------


def test_build_result_translation_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    ext = YtDlpTranscriptExtractor(Config(
        languages=["fr"],
        allow_generated=True,
        allow_translation=True,
        max_retries=0,
    ))
    # Put english subs in the subtitles dict so _pick_any_english finds it
    # via the subtitles pool (is_generated=False path, avoids the `in auto` check)
    info: dict[str, Any] = {
        "subtitles": {"en": [{"ext": "json3", "url": "http://x"}]},
        "automatic_captions": {},
    }
    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hello"}]}]}).encode()
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: cap)

    meta = VideoMetadata(video_id="abcdefghijk")
    res = ext._build_result_from_info(meta, info)
    assert res.success
    assert res.is_generated is False


def test_build_result_translation_fallback_auto_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Translation fallback via auto_captions → is_generated=True."""
    import yt_transcript_pro.ytdlp_extractor as yem

    ext = YtDlpTranscriptExtractor(Config(
        languages=["fr"],
        allow_generated=True,
        allow_translation=True,
        max_retries=0,
    ))
    info: dict[str, Any] = {
        "subtitles": {},
        "automatic_captions": {"en": [{"ext": "json3", "url": "http://x"}]},
    }
    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hello"}]}]}).encode()
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: cap)

    meta = VideoMetadata(video_id="abcdefghijk")
    res = ext._build_result_from_info(meta, info)
    assert res.success
    assert res.is_generated is True


def test_build_result_no_captions(monkeypatch: pytest.MonkeyPatch) -> None:
    ext = YtDlpTranscriptExtractor(Config(
        languages=["fr"],
        allow_generated=False,
        allow_translation=False,
        max_retries=0,
    ))
    info: dict[str, Any] = {
        "subtitles": {},
        "automatic_captions": {},
    }
    meta = VideoMetadata(video_id="abcdefghijk")
    res = ext._build_result_from_info(meta, info)
    assert not res.success
    assert "No captions" in (res.error or "")


def test_build_result_download_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    ext = YtDlpTranscriptExtractor(Config(max_retries=0))
    info: dict[str, Any] = {
        "subtitles": {"en": [{"ext": "json3", "url": "http://x"}]},
        "automatic_captions": {},
    }
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: (_ for _ in ()).throw(
        urllib.error.URLError("timeout")
    ))
    meta = VideoMetadata(video_id="abcdefghijk")
    res = ext._build_result_from_info(meta, info)
    assert not res.success
    assert "caption download failed" in (res.error or "")


def test_build_result_download_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    ext = YtDlpTranscriptExtractor(Config(max_retries=0))
    info: dict[str, Any] = {
        "subtitles": {"en": [{"ext": "json3", "url": "http://x"}]},
        "automatic_captions": {},
    }
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("unexpected")
    ))
    meta = VideoMetadata(video_id="abcdefghijk")
    res = ext._build_result_from_info(meta, info)
    assert not res.success
    assert "caption download failed" in (res.error or "")


def test_build_result_empty_captions(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    ext = YtDlpTranscriptExtractor(Config(max_retries=0))
    info: dict[str, Any] = {
        "subtitles": {"en": [{"ext": "json3", "url": "http://x"}]},
        "automatic_captions": {},
    }
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: b'{"events": []}')
    meta = VideoMetadata(video_id="abcdefghijk")
    res = ext._build_result_from_info(meta, info)
    assert not res.success
    assert "empty" in (res.error or "").lower()


# ---------------------------------------------------------------------------
# fetch_one: generic exception path, info=None, no subs
# ---------------------------------------------------------------------------


def test_fetch_one_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            raise RuntimeError("unexpected error")

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    ext = YtDlpTranscriptExtractor(Config(max_retries=0), player_clients=["android"])
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success


def test_fetch_one_info_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> None:
            return None

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    ext = YtDlpTranscriptExtractor(Config(max_retries=0), player_clients=["android"])
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success


def test_fetch_one_no_usable_subs(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            return {"subtitles": {}, "automatic_captions": {}}

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    ext = YtDlpTranscriptExtractor(
        Config(max_retries=0, allow_translation=False),
        player_clients=["android"],
    )
    res = ext.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success


# ---------------------------------------------------------------------------
# async: throttle + retry
# ---------------------------------------------------------------------------


def test_async_fetch_one_antibot_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
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
            raise DownloadError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    ext = YtDlpTranscriptExtractor(
        Config(max_retries=1, retry_initial_delay=0.01, retry_max_delay=0.02),
        player_clients=["android"],
    )
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success
    assert ext._consecutive_throttles > 0


def test_ytdlp_note_success_initializes_lock() -> None:
    ext = YtDlpTranscriptExtractor(Config())
    assert ext._throttle_lock is None
    asyncio.run(ext._note_success())
    assert ext._throttle_lock is not None


def test_ytdlp_http_get_with_user_agent_override(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_transcript_pro.ytdlp_extractor as yem

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
        yem,
        "_pick_browser_profile",
        lambda: {"User-Agent": "default", "Accept-Language": "en"},
    )
    monkeypatch.setattr(
        yem.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )

    assert (
        yem._http_get("https://www.youtube.com/watch?v=abcdefghijk", ua="custom")
        == b"ok"
    )


def test_async_fetch_many_progress_callback_error(monkeypatch: pytest.MonkeyPatch) -> None:
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

    cap = json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "x"}]}]}).encode()
    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(yem, "_http_get", lambda *a, **k: cap)

    def bad_progress(done: int, total: int, res: TranscriptResult) -> None:
        raise ValueError("callback crash")

    ext = YtDlpTranscriptExtractor(Config(concurrency=1, max_retries=0))
    vids = [VideoMetadata(video_id="abcdefghijk")]
    results = asyncio.run(ext.fetch_many(vids, progress=bad_progress))
    assert len(results) == 1


# ---------------------------------------------------------------------------
# _build_ydl_opts: proxy, cookies, user_agent
# ---------------------------------------------------------------------------


def test_build_ydl_opts_with_proxy() -> None:
    ext = YtDlpTranscriptExtractor(Config(proxy="http://proxy:8080"))
    opts = ext._build_ydl_opts()
    assert opts["proxy"] == "http://proxy:8080"


def test_build_ydl_opts_with_cookies(tmp_path: pytest.TempPathFactory) -> None:
    from pathlib import Path
    cookie_file = Path(str(tmp_path)) / "cookies.txt"
    cookie_file.write_text("# Netscape")
    ext = YtDlpTranscriptExtractor(Config(cookies_file=cookie_file))
    opts = ext._build_ydl_opts()
    assert opts["cookiefile"] == str(cookie_file)


def test_build_ydl_opts_with_user_agent() -> None:
    ext = YtDlpTranscriptExtractor(Config(user_agent="MyBot/1.0"))
    opts = ext._build_ydl_opts()
    assert opts["http_headers"]["User-Agent"] == "MyBot/1.0"
