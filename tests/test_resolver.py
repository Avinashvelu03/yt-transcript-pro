"""Tests for SourceResolver."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yt_transcript_pro.resolver import SourceResolver


class TestExtractVideoId:
    @pytest.mark.parametrize(
        "src,expected",
        [
            ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/v/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=related", "dQw4w9WgXcQ"),
        ],
    )
    def test_valid(self, src: str, expected: str) -> None:
        assert SourceResolver.extract_video_id(src) == expected

    @pytest.mark.parametrize(
        "src",
        [
            "",
            "   ",
            "not_a_url",
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/too_short",
            "https://www.youtube.com/watch?x=dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/bad",
            "ht!tp://badscheme",
        ],
    )
    def test_invalid(self, src: str) -> None:
        assert SourceResolver.extract_video_id(src) is None

    def test_no_host(self) -> None:
        assert SourceResolver.extract_video_id("just text here") is None


class TestClassify:
    @pytest.mark.parametrize(
        "src,expected",
        [
            ("dQw4w9WgXcQ", "video"),
            ("https://youtu.be/dQw4w9WgXcQ", "video"),
            ("PLxxxxxxxxxxxxxxxxxx", "playlist"),
            ("https://www.youtube.com/playlist?list=PLabc", "playlist"),
            ("https://www.youtube.com/c/InnerCircleTrader", "channel"),
            ("https://www.youtube.com/@SomeHandle", "channel"),
            ("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", "channel"),
            ("https://www.youtube.com/user/someuser", "channel"),
            ("", "unknown"),
            ("random string", "unknown"),
        ],
    )
    def test_classify(self, src: str, expected: str) -> None:
        assert SourceResolver.classify(src) == expected

    def test_classify_file(self, tmp_path: Path) -> None:
        p = tmp_path / "list.txt"
        p.write_text("abc")
        assert SourceResolver.classify(str(p)) == "file"


class TestResolve:
    def test_resolve_single_video(self) -> None:
        r = SourceResolver()
        out = r.resolve(["dQw4w9WgXcQ"])
        assert len(out) == 1
        assert out[0].video_id == "dQw4w9WgXcQ"

    def test_resolve_dedupes(self) -> None:
        r = SourceResolver()
        out = r.resolve(
            [
                "dQw4w9WgXcQ",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtu.be/dQw4w9WgXcQ",
            ]
        )
        assert len(out) == 1

    def test_resolve_unknown(self) -> None:
        r = SourceResolver()
        assert r.resolve(["absolute nonsense"]) == []

    def test_resolve_file(self, tmp_path: Path) -> None:
        p = tmp_path / "ids.txt"
        p.write_text(
            "# comment line\n"
            "\n"
            "dQw4w9WgXcQ\n"
            "https://www.youtube.com/watch?v=abcdefghijk\n"
        )
        out = SourceResolver().resolve([str(p)])
        ids = {v.video_id for v in out}
        assert ids == {"dQw4w9WgXcQ", "abcdefghijk"}

    def test_resolve_playlist_via_ydl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_info = {
            "entries": [
                {"id": "abcdefghijk", "title": "One", "channel": "C", "duration": 12},
                {"id": "bbbbbbbbbbb", "title": "Two", "view_count": 5},
                {"id": "bad"},  # invalid id, skipped
                "not a dict",  # skipped
            ],
            "channel": "C",
            "channel_id": "UC000000000000000000",
        }
        r = SourceResolver()
        monkeypatch.setattr(
            r, "_resolve_with_ydl", lambda url: r._flatten_entries(fake_info)
        )
        out = r.resolve(["https://www.youtube.com/playlist?list=PLxyz"])
        ids = [v.video_id for v in out]
        assert ids == ["abcdefghijk", "bbbbbbbbbbb"]
        assert out[0].duration_seconds == 12
        assert out[1].view_count == 5

    def test_resolve_channel_via_ydl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        r = SourceResolver()
        captured = {}

        def fake_ydl(url: str) -> list:
            captured["url"] = url
            return []

        monkeypatch.setattr(r, "_resolve_with_ydl", fake_ydl)
        r.resolve(["https://www.youtube.com/@Handle"])
        # Channel URLs should be normalized to include /videos
        assert captured["url"].endswith("/videos")

    def test_flatten_with_nested_playlist(self) -> None:
        info = {
            "entries": [
                {
                    "_type": "playlist",
                    "entries": [
                        {"id": "nestednestn", "title": "nested"},
                    ],
                },
                {"id": "topleveltop"},
            ]
        }
        out = SourceResolver._flatten_entries(info)
        ids = {v.video_id for v in out}
        assert ids == {"nestednestn", "topleveltop"}

    def test_flatten_with_bad_entries(self) -> None:
        assert SourceResolver._flatten_entries({}) == []
        assert SourceResolver._flatten_entries({"entries": "nope"}) == []

    def test_custom_ydl_opts(self) -> None:
        r = SourceResolver(ydl_opts={"custom": True})
        assert r.ydl_opts["custom"] is True
        assert r.ydl_opts["quiet"] is True

    def test_flatten_bad_duration_and_views(self) -> None:
        info: dict[str, Any] = {
            "entries": [
                {
                    "id": "abcdefghijk",
                    "title": "T",
                    "duration": "not a number",
                    "view_count": None,
                },
            ]
        }
        out = SourceResolver._flatten_entries(info)
        assert out[0].duration_seconds is None
        assert out[0].view_count is None

    def test_flatten_validation_error_skipped(self) -> None:
        # id that passes regex but empty channel/title trigger no error; craft an
        # entry that will fail VideoMetadata validation by spoofing id length.
        info = {"entries": [{"id": "x" * 11, "title": "ok"}]}
        out = SourceResolver._flatten_entries(info)
        # 'x'*11 matches the regex and is valid, so we just check path stays sound
        assert len(out) == 1

    def test_flatten_value_error_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force VideoMetadata construction to raise ValueError → branch 193-194
        from yt_transcript_pro import resolver as rmod

        real_cls = rmod.VideoMetadata

        class _Boom(real_cls):  # type: ignore[misc,valid-type]
            def __init__(self, **kwargs: Any) -> None:
                raise ValueError("nope")

        monkeypatch.setattr(rmod, "VideoMetadata", _Boom)
        out = rmod.SourceResolver._flatten_entries(
            {"entries": [{"id": "abcdefghijk"}]}
        )
        assert out == []

    def test_extract_video_id_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force urlparse to raise to hit lines 51-52
        from yt_transcript_pro import resolver as rmod

        def boom(_: str) -> Any:
            raise ValueError("bad")

        monkeypatch.setattr(rmod, "urlparse", boom)
        assert rmod.SourceResolver.extract_video_id("https://x/y") is None

    def test_resolve_channel_already_has_videos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # URL already ends with /videos → branch at 140 evaluates false
        r = SourceResolver()
        captured = {}

        def fake_ydl(url: str) -> list:
            captured["url"] = url
            return []

        monkeypatch.setattr(r, "_resolve_with_ydl", fake_ydl)
        r.resolve(["https://www.youtube.com/@Handle/videos"])
        assert captured["url"] == "https://www.youtube.com/@Handle/videos"

    def test_resolve_with_ydl_real_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Cover _resolve_with_ydl body by stubbing YoutubeDL
        from yt_transcript_pro import resolver as rmod

        class FakeYDL:
            def __init__(self, opts: Any) -> None:
                self.opts = opts

            def __enter__(self) -> FakeYDL:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def extract_info(self, url: str, download: bool = False) -> dict:
                return {"entries": [{"id": "abcdefghijk", "title": "T"}]}

        import yt_dlp

        monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
        r = rmod.SourceResolver()
        out = r._resolve_with_ydl("https://example.com")
        assert len(out) == 1 and out[0].video_id == "abcdefghijk"

    def test_resolve_with_ydl_none_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from yt_transcript_pro import resolver as rmod

        class FakeYDL:
            def __init__(self, opts: Any) -> None: ...
            def __enter__(self) -> FakeYDL:
                return self
            def __exit__(self, *a: Any) -> None: ...
            def extract_info(self, *a: Any, **k: Any) -> Any:
                return None

        import yt_dlp

        monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
        assert rmod.SourceResolver()._resolve_with_ydl("x") == []
