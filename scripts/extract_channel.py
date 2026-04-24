#!/usr/bin/env python3
"""Convenience wrapper: extract an entire channel into a single text file.

Usage:
    python scripts/extract_channel.py <channel-url> [out_file] [--concurrency N]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from yt_transcript_pro import (
    Config,
    FormatWriter,
    SourceResolver,
    TranscriptExtractor,
)

console = Console()


async def run(args: argparse.Namespace) -> int:
    cfg = Config(
        concurrency=args.concurrency,
        max_retries=args.retries,
        output_dir=Path(args.out).parent or Path(),
        output_format="txt",
        combine_into_single_file=True,
        include_metadata_header=True,
        include_timestamps=args.timestamps,
        resume=True,
        checkpoint_file=Path(args.out).with_suffix(".checkpoint.json"),
    )

    console.print(f"[cyan]Resolving videos from[/cyan] {args.source}")
    resolver = SourceResolver()
    videos = resolver.resolve([args.source])
    if args.max_videos:
        videos = videos[: args.max_videos]
    if not videos:
        console.print("[red]No videos resolved.[/red]")
        return 2
    console.print(f"[green]Found {len(videos)} videos.[/green]")

    extractor = TranscriptExtractor(cfg)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching", total=len(videos))

        def on_progress(done: int, total: int, res) -> None:  # type: ignore[no-untyped-def]
            mark = "✓" if res.success else "✗"
            progress.update(task, completed=done, description=f"[{mark}] {res.metadata.video_id}")

        results = await extractor.fetch_many(videos, progress=on_progress)

    ok = sum(1 for r in results if r.success)
    fail = len(results) - ok
    console.rule("[bold]Summary")
    console.print(f"[green]Success:[/green] {ok}    [red]Failed:[/red] {fail}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = FormatWriter(cfg)
    sep = "\n\n" + ("=" * 80) + "\n\n"
    parts = [writer.render(r, "txt") for r in results if r.success]
    out_path.write_text(sep.join(parts), encoding="utf-8")
    console.print(f"[bold cyan]Combined transcript → {out_path}[/bold cyan]")
    console.print(f"[dim]{out_path.stat().st_size / 1024:.1f} KB[/dim]")

    # Also dump a CSV index for diagnostics
    import csv as _csv

    index_path = out_path.with_suffix(".index.csv")
    with index_path.open("w", encoding="utf-8", newline="") as f:
        writer_ = _csv.writer(f, quoting=_csv.QUOTE_MINIMAL)
        writer_.writerow(
            ["video_id", "title", "success", "language", "words", "error"]
        )
        for r in results:
            # Collapse newlines in error messages so each row is one CSV line
            err = (r.error or "").replace("\n", " ").replace("\r", " ")[:400]
            writer_.writerow(
                [
                    r.metadata.video_id,
                    r.metadata.title or "",
                    r.success,
                    r.language or "",
                    r.word_count,
                    err,
                ]
            )
    console.print(f"[dim]Index → {index_path}[/dim]")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", help="Channel / playlist / video URL")
    p.add_argument("--out", default="output/combined.txt")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--max-videos", type=int, default=None)
    p.add_argument("--timestamps", action="store_true")
    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
