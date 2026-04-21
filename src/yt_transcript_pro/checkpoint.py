"""Checkpointing to allow resume after interruption."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


class Checkpoint:
    """Tracks completed video IDs in a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._done: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("done"), list):
                    self._done = {str(x) for x in data["done"]}
            except (OSError, json.JSONDecodeError):
                self._done = set()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"done": sorted(self._done)}, indent=2),
            encoding="utf-8",
        )

    def is_done(self, video_id: str) -> bool:
        return video_id in self._done

    def mark_done(self, video_id: str) -> None:
        self._done.add(video_id)

    def mark_many(self, video_ids: Iterable[str]) -> None:
        self._done.update(video_ids)

    def __contains__(self, video_id: object) -> bool:
        return isinstance(video_id, str) and video_id in self._done

    def __len__(self) -> int:
        return len(self._done)
