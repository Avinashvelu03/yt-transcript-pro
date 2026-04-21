"""Tests for the Checkpoint helper."""

from __future__ import annotations

from pathlib import Path

from yt_transcript_pro.checkpoint import Checkpoint


def test_fresh_checkpoint(tmp_path: Path) -> None:
    c = Checkpoint(tmp_path / "ckpt.json")
    assert len(c) == 0
    assert not c.is_done("abc")


def test_mark_and_save_reload(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    c = Checkpoint(path)
    c.mark_done("a")
    c.mark_many(["b", "c"])
    c.save()
    assert "a" in c and len(c) == 3

    # Reload
    c2 = Checkpoint(path)
    assert c2.is_done("a")
    assert c2.is_done("c")


def test_corrupt_file_handled(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    path.write_text("{not json")
    c = Checkpoint(path)
    assert len(c) == 0


def test_non_dict_json_handled(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    path.write_text("[1,2,3]")
    c = Checkpoint(path)
    assert len(c) == 0


def test_contains_non_string(tmp_path: Path) -> None:
    c = Checkpoint(tmp_path / "ckpt.json")
    c.mark_done("x")
    assert 123 not in c  # type: ignore[operator]


def test_save_creates_parent(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c.json"
    c = Checkpoint(deep)
    c.mark_done("x")
    c.save()
    assert deep.exists()
