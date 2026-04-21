"""Command-line interface for yt-transcript-pro."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
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

from yt_transcript_pro import __version__
from yt_transcript_pro.checkpoint import Checkpoint
from yt_transcript_pro.config import Config
from yt_transcript_pro.extractor import TranscriptExtractor
from yt_transcript_pro.models import TranscriptResult, VideoMetadata
from yt_transcript_pro.resolver import SourceResolver
from yt_transcript_pro.writers import FormatWriter

app = typer.Typer(
    name="yt-transcript-pro",
    help="Production-grade YouTube transcript extractor.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"yt-transcript-pro {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool | None = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """yt-transcript-pro: extract YouTube transcripts at scale."""


@app.command()
def extract(
    sources: list[str] = typer.Argument(
        ..., help="Video URLs/IDs, playlist URLs, channel URLs, or paths to text files.",
    ),
    output_dir: Path = typer.Option(
        Path("output"), "--output-dir", "-o", help="Directory to write output files.",
    ),
    fmt: str = typer.Option(
        "txt", "--format", "-f",
        help="Output format: txt | json | srt | vtt | md | csv | all",
    ),
    combine: bool = typer.Option(
        False, "--combine", "-C",
        help="Combine all transcripts into a single file.",
    ),
    combined_name: str = typer.Option(
        "combined", "--combined-name", help="Filename stem for combined output.",
    ),
    concurrency: int = typer.Option(5, "--concurrency", "-c", min=1, max=64),
    max_videos: int | None = typer.Option(
        None, "--max-videos", "-n", help="Limit total videos processed.",
    ),
    languages: str = typer.Option(
        "en,en-US,en-GB", "--languages", "-l",
        help="Comma-separated language preference list.",
    ),
    timestamps: bool = typer.Option(
        False, "--timestamps/--no-timestamps",
        help="Include timestamps in textual outputs.",
    ),
    metadata_header: bool = typer.Option(
        True, "--metadata-header/--no-metadata-header",
        help="Include metadata header in text outputs.",
    ),
    allow_generated: bool = typer.Option(
        True, "--allow-generated/--manual-only",
        help="Allow auto-generated transcripts as fallback.",
    ),
    resume: bool = typer.Option(
        True, "--resume/--no-resume", help="Skip videos already completed.",
    ),
    checkpoint: Path | None = typer.Option(
        None, "--checkpoint", help="Path to checkpoint JSON file.",
    ),
    retries: int = typer.Option(4, "--retries", min=0, max=20),
    proxy: str | None = typer.Option(
        None, "--proxy",
        help="HTTP(S) proxy URL (e.g. http://user:pass@host:port) to bypass YouTube IP blocks.",
    ),
    webshare_user: str | None = typer.Option(
        None, "--webshare-user", help="Webshare rotating-residential proxy username.",
    ),
    webshare_pass: str | None = typer.Option(
        None, "--webshare-pass", help="Webshare rotating-residential proxy password.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Extract transcripts from one or more sources."""
    _setup_logging(verbose)

    cfg = Config(
        concurrency=concurrency,
        max_retries=retries,
        languages=[s.strip() for s in languages.split(",") if s.strip()],
        allow_generated=allow_generated,
        output_dir=output_dir,
        output_format=fmt,
        combine_into_single_file=combine,
        include_timestamps=timestamps,
        include_metadata_header=metadata_header,
        max_videos=max_videos,
        resume=resume,
        checkpoint_file=checkpoint,
        proxy=proxy,
        webshare_proxy_username=webshare_user,
        webshare_proxy_password=webshare_pass,
        verbose=verbose,
    )

    console.rule("[bold cyan]yt-transcript-pro")
    console.print(f"[dim]Resolving {len(sources)} source(s)…[/dim]")

    resolver = SourceResolver()
    videos = resolver.resolve(sources)
    if cfg.max_videos:
        videos = videos[: cfg.max_videos]
    if not videos:
        console.print("[red]No videos resolved from sources.[/red]")
        raise typer.Exit(code=2)
    console.print(f"[green]Resolved {len(videos)} video(s).[/green]")

    ckpt: Checkpoint | None = None
    if cfg.resume:
        ckpt_path = cfg.checkpoint_file or (cfg.output_dir / ".yttp-checkpoint.json")
        ckpt = Checkpoint(ckpt_path)
        before = len(videos)
        videos = [v for v in videos if not ckpt.is_done(v.video_id)]
        skipped = before - len(videos)
        if skipped:
            console.print(f"[yellow]Resume: skipping {skipped} already completed.[/yellow]")

    extractor = TranscriptExtractor(cfg)
    writer = FormatWriter(cfg)
    results = _run(extractor, writer, videos, cfg, ckpt)

    ok = sum(1 for r in results if r.success)
    fail = len(results) - ok
    console.rule("[bold]Summary")
    console.print(f"[green]Success:[/green] {ok}    [red]Failed:[/red] {fail}")
    if cfg.combine_into_single_file:
        formats = _formats_to_write(cfg.output_format)
        for f in formats:
            path = writer.write_combined(results, f, filename=combined_name)
            console.print(f"[cyan]Combined → {path}[/cyan]")


def _formats_to_write(fmt: str) -> list[str]:
    if fmt == "all":
        return ["txt", "json", "srt", "vtt", "md", "csv"]
    return [fmt]


def _run(
    extractor: TranscriptExtractor,
    writer: FormatWriter,
    videos: list[VideoMetadata],
    cfg: Config,
    ckpt: Checkpoint | None,
) -> list[TranscriptResult]:
    formats = _formats_to_write(cfg.output_format)
    results: list[TranscriptResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task_id = progress.add_task("Extracting", total=len(videos))

        def on_progress(done: int, total: int, res: TranscriptResult) -> None:
            status = "✓" if res.success else "✗"
            progress.update(
                task_id,
                completed=done,
                description=f"[{status}] {res.metadata.video_id}",
            )
            if not cfg.combine_into_single_file and res.success:
                for f in formats:
                    try:
                        writer.write(res, f)
                    except OSError as exc:  # pragma: no cover - io edge case
                        logging.warning("Write failed for %s: %s", res.metadata.video_id, exc)
            if ckpt is not None and res.success:
                ckpt.mark_done(res.metadata.video_id)
                # Persist periodically (cheap, small file)
                if done % 10 == 0 or done == total:
                    ckpt.save()

        results = asyncio.run(extractor.fetch_many(videos, progress=on_progress))

    if ckpt is not None:
        ckpt.save()
    return results


@app.command()
def resolve(
    sources: list[str] = typer.Argument(..., help="Sources to resolve (no download)."),
    max_videos: int | None = typer.Option(None, "--max-videos", "-n"),
) -> None:
    """Only resolve sources into video IDs (for inspection / piping)."""
    resolver = SourceResolver()
    videos = resolver.resolve(sources)
    if max_videos:
        videos = videos[:max_videos]
    for v in videos:
        sys.stdout.write(f"{v.video_id}\t{v.title}\n")


if __name__ == "__main__":  # pragma: no cover
    app()
