"""Tests for auto_extractor.py — covers async cascade, fetch_many, circuit breaker logging."""

from __future__ import annotations

import asyncio

import pytest

from yt_transcript_pro.auto_extractor import _BREAKER_THRESHOLD, AutoTranscriptExtractor
from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata


def _ok(vid: str = "abcdefghijk") -> TranscriptResult:
    return TranscriptResult(
        metadata=VideoMetadata(video_id=vid),
        entries=[TranscriptEntry(text="ok", start=0.0, duration=1.0)],
        language="en",
        success=True,
    )


def _fail(vid: str = "abcdefghijk", error: str = "transient: 429") -> TranscriptResult:
    return TranscriptResult(
        metadata=VideoMetadata(video_id=vid),
        success=False,
        error=error,
    )


# ---------------------------------------------------------------------------
# fetch_one: all backends fail → returns last error
# ---------------------------------------------------------------------------


def test_all_backends_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch", "ytdlp", "api"],
    )
    monkeypatch.setattr(auto._watch, "fetch_one", lambda m: _fail(error="watch: blocked"))
    monkeypatch.setattr(auto._ytdlp, "fetch_one", lambda m: _fail(error="ytdlp: blocked"))
    monkeypatch.setattr(auto._api, "fetch_one", lambda m: _fail(error="api: blocked"))
    res = auto.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "api: blocked" in (res.error or "")


def test_all_backends_fail_fallback_result() -> None:
    """When no backend is even tried (all breakers open), returns synthetic error."""
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch"],
    )
    # Trip the breaker
    auto._breaker_failures["watch"] = _BREAKER_THRESHOLD
    res = auto.fetch_one(VideoMetadata(video_id="abcdefghijk"))
    assert not res.success
    assert "all backends failed" in (res.error or "")


# ---------------------------------------------------------------------------
# fetch_one_async: cascade + breaker logging
# ---------------------------------------------------------------------------


def test_async_cascade_success(monkeypatch: pytest.MonkeyPatch) -> None:
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["ytdlp", "watch"],
    )

    async def ok_async(meta: VideoMetadata) -> TranscriptResult:
        return _ok(meta.video_id)

    async def fail_async(meta: VideoMetadata) -> TranscriptResult:
        return _fail(meta.video_id)

    monkeypatch.setattr(auto._ytdlp, "fetch_one_async", fail_async)
    monkeypatch.setattr(auto._watch, "fetch_one_async", ok_async)

    res = asyncio.run(auto.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert res.success


def test_async_cascade_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["ytdlp", "watch"],
    )

    async def perm_async(meta: VideoMetadata) -> TranscriptResult:
        return _fail(meta.video_id, error="Private video: unavailable")

    async def should_not_call(meta: VideoMetadata) -> TranscriptResult:
        raise AssertionError("should not be called")

    monkeypatch.setattr(auto._ytdlp, "fetch_one_async", perm_async)
    monkeypatch.setattr(auto._watch, "fetch_one_async", should_not_call)

    res = asyncio.run(auto.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success
    assert "Private" in (res.error or "")


def test_async_circuit_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """After threshold failures, backend is skipped in async path."""
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch", "ytdlp"],
    )
    watch_calls = {"n": 0}

    async def bad_watch(meta: VideoMetadata) -> TranscriptResult:
        watch_calls["n"] += 1
        return _fail(meta.video_id, error="429 too many")

    async def good_ytdlp(meta: VideoMetadata) -> TranscriptResult:
        return _ok(meta.video_id)

    monkeypatch.setattr(auto._watch, "fetch_one_async", bad_watch)
    monkeypatch.setattr(auto._ytdlp, "fetch_one_async", good_ytdlp)

    async def run_many() -> None:
        for _ in range(_BREAKER_THRESHOLD + 5):
            res = await auto.fetch_one_async(VideoMetadata(video_id="abcdefghijk"))
            assert res.success

    asyncio.run(run_many())
    # Watch should have stopped after threshold
    assert watch_calls["n"] == _BREAKER_THRESHOLD


def test_async_all_fail_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all backends fail, returns last error."""
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch"],
    )

    async def fail_async(meta: VideoMetadata) -> TranscriptResult:
        return _fail(meta.video_id)

    monkeypatch.setattr(auto._watch, "fetch_one_async", fail_async)
    res = asyncio.run(auto.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success


def test_async_all_breakers_open() -> None:
    """When all breakers open in async, returns synthetic error."""
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch"],
    )
    auto._breaker_failures["watch"] = _BREAKER_THRESHOLD
    res = asyncio.run(auto.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success
    assert "all backends failed" in (res.error or "")


# ---------------------------------------------------------------------------
# fetch_many
# ---------------------------------------------------------------------------


def test_fetch_many_empty() -> None:
    auto = AutoTranscriptExtractor(Config())
    results = asyncio.run(auto.fetch_many([]))
    assert results == []


def test_fetch_many_with_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    auto = AutoTranscriptExtractor(
        Config(concurrency=2, max_retries=0),
        backend_order=["watch"],
    )

    async def ok_async(meta: VideoMetadata) -> TranscriptResult:
        return _ok(meta.video_id)

    monkeypatch.setattr(auto._watch, "fetch_one_async", ok_async)

    calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int, res: TranscriptResult) -> None:
        calls.append((done, total))

    vids = [VideoMetadata(video_id=f"aaaaaaaaa{i:02d}") for i in range(3)]
    results = asyncio.run(auto.fetch_many(vids, progress=on_progress))
    assert len(results) == 3
    assert len(calls) == 3


def test_fetch_many_progress_callback_error(monkeypatch: pytest.MonkeyPatch) -> None:
    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch"],
    )

    async def ok_async(meta: VideoMetadata) -> TranscriptResult:
        return _ok(meta.video_id)

    monkeypatch.setattr(auto._watch, "fetch_one_async", ok_async)

    def bad_progress(done: int, total: int, res: TranscriptResult) -> None:
        raise ValueError("boom")

    vids = [VideoMetadata(video_id="abcdefghijk")]
    results = asyncio.run(auto.fetch_many(vids, progress=bad_progress))
    assert len(results) == 1
