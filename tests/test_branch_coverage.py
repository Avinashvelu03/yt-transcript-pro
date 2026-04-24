"""Tests targeting the exact remaining partial branches."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from yt_transcript_pro.config import Config
from yt_transcript_pro.models import TranscriptEntry, TranscriptResult, VideoMetadata

# ---------------------------------------------------------------------------
# auto_extractor: fetch_many WITHOUT progress (166->171: progress is None)
# ---------------------------------------------------------------------------


def test_auto_fetch_many_no_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor

    auto = AutoTranscriptExtractor(
        Config(concurrency=1, max_retries=0),
        backend_order=["watch"],
    )
    good = TranscriptResult(
        metadata=VideoMetadata(video_id="abcdefghijk"),
        entries=[TranscriptEntry(text="ok", start=0.0, duration=1.0)],
        language="en",
        success=True,
    )

    async def ok_async(meta: VideoMetadata) -> TranscriptResult:
        return good

    monkeypatch.setattr(auto._watch, "fetch_one_async", ok_async)
    # No progress callback → exercises the False branch of `if progress is not None`
    results = asyncio.run(auto.fetch_many([VideoMetadata(video_id="abcdefghijk")]))
    assert len(results) == 1


# ---------------------------------------------------------------------------
# watch_extractor: fetch_many WITHOUT progress (376->381: progress is None)
# ---------------------------------------------------------------------------


def test_watch_fetch_many_no_progress(monkeypatch: pytest.MonkeyPatch) -> None:
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
    from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
    ext = WatchPageTranscriptExtractor(Config(concurrency=1, max_retries=0))
    # No progress callback
    results = asyncio.run(ext.fetch_many([VideoMetadata(video_id="abcdefghijk")]))
    assert len(results) == 1


# ---------------------------------------------------------------------------
# ytdlp_extractor: fetch_many WITHOUT progress
# ---------------------------------------------------------------------------


def test_ytdlp_fetch_many_no_progress(monkeypatch: pytest.MonkeyPatch) -> None:
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

    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor
    ext = YtDlpTranscriptExtractor(Config(concurrency=1, max_retries=0))
    # No progress callback
    results = asyncio.run(ext.fetch_many([VideoMetadata(video_id="abcdefghijk")]))
    assert len(results) == 1


# ---------------------------------------------------------------------------
# watch_extractor: _pick_track translation with non-translatable track (286->285)
# ---------------------------------------------------------------------------


def test_watch_pick_track_translation_not_translatable() -> None:
    from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor

    cfg = Config(languages=["fr"], allow_generated=True, allow_translation=True)
    ext = WatchPageTranscriptExtractor(cfg)
    # Track is NOT translatable → the for loop falls through
    tracks = [{"languageCode": "ja", "kind": "manual", "baseUrl": "http://a", "isTranslatable": False}]
    result = ext._pick_track(tracks)
    # Should still get None since no English fallback and not translatable
    assert result is None


# ---------------------------------------------------------------------------
# cli: on_progress with failed result (331->340: res.success=False)
# ---------------------------------------------------------------------------


def test_cli_on_progress_failed_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from typer.testing import CliRunner

    from yt_transcript_pro import cli as cli_module
    from yt_transcript_pro.cli import app

    runner = CliRunner()
    metas = [
        VideoMetadata(video_id="aaaaaaaaa01", url="u"),
        VideoMetadata(video_id="aaaaaaaaa02", url="u"),
    ]
    monkeypatch.setattr(cli_module.SourceResolver, "resolve", lambda self, s: metas)

    async def fake_fetch_many(self: Any, videos: Any, progress: Any = None) -> list:
        results = []
        for i, v in enumerate(videos, 1):
            r = TranscriptResult(
                metadata=v,
                entries=[],
                language=None,
                success=False,
                error="No captions",
            )
            results.append(r)
            if progress:
                progress(i, len(videos), r)
        return results

    monkeypatch.setattr(
        cli_module.TranscriptExtractor, "fetch_many", fake_fetch_many
    )

    outdir = tmp_path / "out"
    result = runner.invoke(
        app,
        ["extract", "X", "-o", str(outdir), "--no-resume", "--backend", "api"],
    )
    # Should complete without error despite all results failing
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# ytdlp: _pick_track with best_format returning None (635->632)
# ---------------------------------------------------------------------------


def test_ytdlp_pick_track_best_format_none() -> None:
    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor

    # Pool has tracks but none with a URL
    pool = {"en": [{"ext": "json3"}]}  # no "url" key
    result = YtDlpTranscriptExtractor._pick_track(pool, ["en"])
    assert result is None


def test_ytdlp_pick_track_prefix_best_format_none() -> None:
    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor

    # Prefix match found but best_format returns None
    pool = {"en-US": [{"ext": "json3"}]}  # no "url" key
    result = YtDlpTranscriptExtractor._pick_track(pool, ["en"])
    assert result is None


def test_ytdlp_pick_any_english_best_format_none() -> None:
    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor

    pool = {"en-GB": [{"ext": "json3"}]}  # no "url" key
    result = YtDlpTranscriptExtractor._pick_any_english(pool)
    assert result is None


# ---------------------------------------------------------------------------
# ytdlp async: retry with transient (non-permanent, non-antibot) error (699->702)
# ---------------------------------------------------------------------------


def test_ytdlp_async_retry_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
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
            raise DownloadError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(yem, "YoutubeDL", FakeYDL)
    from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor
    ext = YtDlpTranscriptExtractor(
        Config(max_retries=1, retry_initial_delay=0.01, retry_max_delay=0.02),
        player_clients=["android"],
    )
    res = asyncio.run(ext.fetch_one_async(VideoMetadata(video_id="abcdefghijk")))
    assert not res.success


# ---------------------------------------------------------------------------
# _parse_vtt: empty cue after stripping (368->376)
# ---------------------------------------------------------------------------


def test_vtt_empty_cue_after_strip() -> None:
    """VTT cue with only tags/whitespace should be skipped."""
    from yt_transcript_pro.ytdlp_extractor import _parse_vtt

    data = (
        b"WEBVTT\n\n"
        b"00:00:01.000 --> 00:00:03.000\n"
        b"<c.colorFFFFFF>  </c>\n\n"
        b"00:00:03.000 --> 00:00:05.000\n"
        b"actual text\n"
    )
    entries = _parse_vtt(data)
    assert len(entries) == 1
    assert entries[0].text == "actual text"
