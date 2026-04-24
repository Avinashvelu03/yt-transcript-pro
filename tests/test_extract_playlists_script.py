from __future__ import annotations

from pathlib import Path

from extract_playlists import _block_video_id, _block_words, _read_transcript_blocks


def test_read_transcript_blocks_splits_combined_file(tmp_path: Path) -> None:
    combined = tmp_path / "combined.txt"
    combined.write_text(
        "\n".join(
            [
                "# Title: One",
                "# Video ID: AAAAAAAAAAA",
                "# Words: 2",
                "",
                "hello world",
                "",
                "=" * 80,
                "",
                "# Title: Two",
                "# Video ID: BBBBBBBBBBB",
                "# Words: 3",
                "",
                "one two three",
            ]
        ),
        encoding="utf-8",
    )

    blocks = _read_transcript_blocks(combined)

    assert set(blocks) == {"AAAAAAAAAAA", "BBBBBBBBBBB"}
    assert _block_words(blocks["AAAAAAAAAAA"]) == 2
    assert _block_video_id(blocks["BBBBBBBBBBB"]) == "BBBBBBBBBBB"
