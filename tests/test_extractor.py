"""Tests for the TranscriptExtractor."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

from yt_transcript_pro.config import Config
from yt_transcript_pro.extractor import TranscriptExtractor
from yt_transcript_pro.models import VideoMetadata


def _fake_transcript_list(transcript: Any, iterable: list[Any] | None = None) -> MagicMock:
    tl = MagicMock()
    tl.find_manually_created_transcript = MagicMock(return_value=transcript)
    tl.find_generated_transcript = MagicMock(return_value=transcript)
    tl.__iter__ = lambda self: iter(iterable or [transcript])
    return tl


def _fake_transcript(
    raw: list[dict],
    language_code: str = "en",
    is_generated: bool = False,
    *,
    is_translatable: bool = False,
) -> MagicMock:
    t = MagicMock()
    fetched = MagicMock()
    fetched.to_raw_data = MagicMock(return_value=raw)
    t.fetch = MagicMock(return_value=fetched)
    t.language_code = language_code
    t.is_generated = is_generated
    t.is_translatable = is_translatable
    t.translate = MagicMock(return_value=t)
    return t


@pytest.fixture()
def meta() -> VideoMetadata:
    return VideoMetadata(video_id="abcdefghijk", url="u")


class TestFetchOne:
    def test_success_manual(self, meta: VideoMetadata, base_config: Config) -> None:
        raw = [
            {"text": "hi", "start": 0.0, "duration": 1.0},
            {"text": "world", "start": 1.0, "duration": 1.5},
        ]
        t = _fake_transcript(raw)
        api = MagicMock()
        api.list = MagicMock(return_value=_fake_transcript_list(t))
        ext = TranscriptExtractor(base_config, api=api)
        res = ext.fetch_one(meta)
        assert res.success
        assert res.language == "en"
        assert len(res.entries) == 2

    def test_raw_list_without_to_raw_data(
        self, meta: VideoMetadata, base_config: Config
    ) -> None:
        # Simulate older API where .fetch() returns a plain list
        raw = [{"text": "x", "start": 0.0, "duration": 1.0}]
        t = MagicMock()
        t.fetch = MagicMock(return_value=raw)
        t.language_code = "en"
        t.is_generated = True
        api = MagicMock()
        api.list = MagicMock(return_value=_fake_transcript_list(t))
        ext = TranscriptExtractor(base_config, api=api)
        res = ext.fetch_one(meta)
        assert res.success and res.is_generated

    def test_transcripts_disabled(self, meta: VideoMetadata, base_config: Config) -> None:
        api = MagicMock()
        api.list = MagicMock(side_effect=TranscriptsDisabled("abcdefghijk"))
        ext = TranscriptExtractor(base_config, api=api)
        res = ext.fetch_one(meta)
        assert not res.success
        assert "TranscriptsDisabled" in (res.error or "")

    def test_no_transcript_found_then_generated_fallback(
        self, meta: VideoMetadata, base_config: Config
    ) -> None:
        raw = [{"text": "gen", "start": 0.0, "duration": 1.0}]
        t = _fake_transcript(raw, is_generated=True)
        tl = MagicMock()
        tl.find_manually_created_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.find_generated_transcript = MagicMock(return_value=t)
        tl.__iter__ = lambda self: iter([t])
        api = MagicMock()
        api.list = MagicMock(return_value=tl)
        ext = TranscriptExtractor(base_config, api=api)
        res = ext.fetch_one(meta)
        assert res.success and res.is_generated

    def test_translation_path(self, base_config: Config, meta: VideoMetadata) -> None:
        # Disallow generated, force translation path
        cfg = Config(
            **{**base_config.__dict__, "allow_generated": False, "allow_translation": True}
        )
        foreign = _fake_transcript(
            [{"text": "hola", "start": 0.0, "duration": 1.0}],
            language_code="es",
            is_translatable=True,
        )
        tl = MagicMock()
        tl.find_manually_created_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.find_generated_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.__iter__ = lambda self: iter([foreign])
        api = MagicMock()
        api.list = MagicMock(return_value=tl)
        ext = TranscriptExtractor(cfg, api=api)
        res = ext.fetch_one(meta)
        assert res.success
        foreign.translate.assert_called_once()

    def test_translation_fails_then_returns_raw(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        cfg = Config(
            **{**base_config.__dict__, "allow_generated": False, "allow_translation": True}
        )
        foreign = _fake_transcript(
            [{"text": "hola", "start": 0.0, "duration": 1.0}],
            language_code="es",
            is_translatable=True,
        )
        foreign.translate = MagicMock(side_effect=RuntimeError("fail"))
        fallback = _fake_transcript(
            [{"text": "ok", "start": 0.0, "duration": 1.0}],
            language_code="de",
            is_translatable=False,
        )
        tl = MagicMock()
        tl.find_manually_created_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.find_generated_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.__iter__ = lambda self: iter([foreign, fallback])
        api = MagicMock()
        api.list = MagicMock(return_value=tl)
        ext = TranscriptExtractor(cfg, api=api)
        res = ext.fetch_one(meta)
        assert res.success
        assert res.language == "de"

    def test_iter_raises_wrapped(self, base_config: Config, meta: VideoMetadata) -> None:
        cfg = Config(**{**base_config.__dict__, "allow_generated": False})
        tl = MagicMock()
        tl.find_manually_created_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )

        def raise_on_iter(_self: Any) -> Any:
            raise RuntimeError("bad")

        tl.__iter__ = raise_on_iter
        api = MagicMock()
        api.list = MagicMock(return_value=tl)
        ext = TranscriptExtractor(cfg, api=api)
        res = ext.fetch_one(meta)
        assert not res.success
        assert "NoTranscriptFound" in (res.error or "")

    def test_iter_empty_raises_no_transcript(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        cfg = Config(**{**base_config.__dict__, "allow_generated": False})
        tl = MagicMock()
        tl.find_manually_created_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.__iter__ = lambda self: iter([])
        api = MagicMock()
        api.list = MagicMock(return_value=tl)
        ext = TranscriptExtractor(cfg, api=api)
        res = ext.fetch_one(meta)
        assert not res.success

    def test_generic_exception_captured(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        api = MagicMock()
        api.list = MagicMock(side_effect=RuntimeError("boom"))
        ext = TranscriptExtractor(base_config, api=api)
        res = ext.fetch_one(meta)
        assert not res.success
        assert "RuntimeError" in (res.error or "")

    def test_could_not_retrieve_reraised(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        api = MagicMock()
        api.list = MagicMock(side_effect=CouldNotRetrieveTranscript("abcdefghijk"))
        ext = TranscriptExtractor(base_config, api=api)
        with pytest.raises(CouldNotRetrieveTranscript):
            ext.fetch_one(meta)


class TestAsync:
    def test_fetch_one_async_success(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        raw = [{"text": "hi", "start": 0.0, "duration": 1.0}]
        t = _fake_transcript(raw)
        api = MagicMock()
        api.list = MagicMock(return_value=_fake_transcript_list(t))
        ext = TranscriptExtractor(base_config, api=api)
        res = asyncio.run(ext.fetch_one_async(meta))
        assert res.success

    def test_fetch_one_async_retries_then_fails(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        api = MagicMock()
        api.list = MagicMock(side_effect=CouldNotRetrieveTranscript("abcdefghijk"))
        cfg = Config(
            **{
                **base_config.__dict__,
                "max_retries": 2,
                "retry_initial_delay": 0.001,
                "retry_max_delay": 0.002,
            }
        )
        ext = TranscriptExtractor(cfg, api=api)
        res = asyncio.run(ext.fetch_one_async(meta))
        assert not res.success
        assert api.list.call_count == 3  # 1 + 2 retries

    def test_fetch_many_empty(self, base_config: Config) -> None:
        ext = TranscriptExtractor(base_config, api=MagicMock())
        assert asyncio.run(ext.fetch_many([])) == []

    def test_fetch_many_without_progress(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        # progress=None branch (180->182)
        raw = [{"text": "hi", "start": 0.0, "duration": 1.0}]
        t = _fake_transcript(raw)
        api = MagicMock()
        api.list = MagicMock(return_value=_fake_transcript_list(t))
        ext = TranscriptExtractor(base_config, api=api)
        out = asyncio.run(ext.fetch_many([meta], progress=None))
        assert len(out) == 1

    def test_fetch_many_with_progress(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        raw = [{"text": "hi", "start": 0.0, "duration": 1.0}]
        t = _fake_transcript(raw)
        api = MagicMock()
        api.list = MagicMock(return_value=_fake_transcript_list(t))
        ext = TranscriptExtractor(base_config, api=api)

        calls: list[tuple[int, int]] = []

        def progress(done: int, total: int, _res: Any) -> None:
            calls.append((done, total))

        m2 = VideoMetadata(video_id="bbbbbbbbbbb", url="u")
        out = asyncio.run(ext.fetch_many([meta, m2], progress=progress))
        assert len(out) == 2
        assert len(calls) == 2
        assert calls[-1][1] == 2


class TestNoTranscriptGeneratedFallbackFails:
    def test_generated_also_raises_no_transcript(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        # Both manual and generated raise NoTranscriptFound → hits 106-107
        tl = MagicMock()
        tl.find_manually_created_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.find_generated_transcript = MagicMock(
            side_effect=NoTranscriptFound("abcdefghijk", ["en"], tl)
        )
        tl.__iter__ = lambda self: iter([])  # empty → NoTranscriptFound
        api = MagicMock()
        api.list = MagicMock(return_value=tl)
        ext = TranscriptExtractor(base_config, api=api)
        res = ext.fetch_one(meta)
        assert not res.success
        assert "NoTranscriptFound" in (res.error or "")


class TestDefaultApi:
    def test_uses_real_api_when_not_provided(self, base_config: Config) -> None:
        ext = TranscriptExtractor(base_config)
        # Real YouTubeTranscriptApi instance should have .list method
        assert hasattr(ext._api, "list")

    def test_builds_with_generic_proxy(self) -> None:
        cfg = Config(proxy="http://user:pass@proxy.example.com:8080")
        ext = TranscriptExtractor(cfg)
        assert hasattr(ext._api, "list")

    def test_builds_with_webshare_proxy(self) -> None:
        cfg = Config(
            webshare_proxy_username="u",
            webshare_proxy_password="p",
        )
        ext = TranscriptExtractor(cfg)
        assert hasattr(ext._api, "list")


class TestPermanentExceptions:
    def test_ip_blocked_not_retried(
        self, base_config: Config, meta: VideoMetadata
    ) -> None:
        from youtube_transcript_api._errors import IpBlocked

        api = MagicMock()
        api.list = MagicMock(side_effect=IpBlocked("abcdefghijk"))
        ext = TranscriptExtractor(base_config, api=api)
        res = ext.fetch_one(meta)
        assert not res.success
        assert "IpBlocked" in (res.error or "")
        # Should be called only once (permanent, no retry)
        assert api.list.call_count == 1
