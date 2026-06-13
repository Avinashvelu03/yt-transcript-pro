"""Deterministic coverage for the watch-page Client-Hint header copy loop.

``_http_get`` copies any ``sec-ch-ua*`` headers carried by the randomly chosen
browser profile. Because the profile is drawn with ``random.choice`` over
``_BROWSER_PROFILES``, whether both the present/absent branches of that copy
loop get exercised used to depend on the draw -- occasionally leaving the
branch partially covered and tripping the 100% coverage gate on a single CI
job (seen on Python 3.11). Pinning a partial-key profile here covers both
branches deterministically on every Python version.
"""

from __future__ import annotations

import pytest

import yt_transcript_pro.watch_extractor as wem


def test_http_get_copies_partial_client_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self) -> None:
            self.headers = {"Content-Encoding": ""}

        def read(self) -> bytes:
            return b"ok"

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    # Two of the seven Client-Hint keys are present and the rest absent, so the
    # copy loop exercises both the "present" (header copied) and "absent" (key
    # skipped) branches in a single call -- independent of which random profile
    # is drawn elsewhere in the suite.
    monkeypatch.setattr(
        wem,
        "_pick_browser_profile",
        lambda: {
            "User-Agent": "default",
            "Accept-Language": "en",
            "sec-ch-ua": '"Chromium";v="136"',
            "sec-ch-ua-mobile": "?0",
        },
    )
    monkeypatch.setattr(
        wem.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )

    assert (
        wem._http_get("https://www.youtube.com/watch?v=abcdefghijk", timeout=1, ua="")
        == b"ok"
    )
