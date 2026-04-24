"""Command-line interface for yt-transcript-pro."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional, cast

import typer
from rich.console import Console

# ---- Windows console UTF-8 workaround ----
# Rich's progress bar uses non-ASCII glyphs (✓ ✗ etc.) that crash on the
# default cp1252 console on Windows. Force UTF-8 output transparently.
if os.name == "nt":  # pragma: no cover — Windows-only
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            cast(Callable[..., object], reconfigure)(
                encoding="utf-8", errors="replace"
            )
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
from yt_transcript_pro.auto_extractor import AutoTranscriptExtractor
from yt_transcript_pro.checkpoint import Checkpoint
from yt_transcript_pro.config import Config
from yt_transcript_pro.extractor import TranscriptExtractor
from yt_transcript_pro.models import TranscriptResult, VideoMetadata
from yt_transcript_pro.resolver import SourceResolver
from yt_transcript_pro.watch_extractor import WatchPageTranscriptExtractor
from yt_transcript_pro.writers import FormatWriter
from yt_transcript_pro.ytdlp_extractor import YtDlpTranscriptExtractor

app = typer.Typer(
    name="yt-transcript-pro",
    help="Production-grade YouTube transcript extractor.",
    add_completion=False,
    no_args_is_help=True,
)
# ``legacy_windows=False`` forces Rich to use the modern VT sequences instead
# of the legacy Windows console path that bombs on Unicode.
console = Console(legacy_windows=False if os.name == "nt" else None)


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
    version: Optional[bool] = typer.Option(
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
    max_videos: Optional[int] = typer.Option(
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
    checkpoint: Optional[Path] = typer.Option(
        None, "--checkpoint", help="Path to checkpoint JSON file.",
    ),
    retries: int = typer.Option(4, "--retries", min=0, max=20),
    proxy: Optional[str] = typer.Option(
        None, "--proxy",
        help="(Optional) HTTP(S) proxy URL, e.g. http://user:pass@host:port.",
    ),
    webshare_user: Optional[str] = typer.Option(
        None, "--webshare-user", help="Webshare rotating-residential proxy username.",
    ),
    webshare_pass: Optional[str] = typer.Option(
        None, "--webshare-pass", help="Webshare rotating-residential proxy password.",
    ),
    cookies: Optional[Path] = typer.Option(
        None, "--cookies",
        help="cookies.txt file (Netscape format) for age-restricted/private videos.",
    ),
    user_agent: Optional[str] = typer.Option(
        None, "--user-agent", help="Override the HTTP User-Agent (default: rotating modern UAs).",
    ),
    backend: str = typer.Option(
        "auto", "--backend", "-b",
        help=(
            "Extraction backend: 'auto' (default, cascades watch→ytdlp→api — most "
            "resilient), 'watch' (scrape /watch HTML, very block-resistant), "
            "'ytdlp' (yt-dlp player API with client rotation), or 'api' "
            "(legacy youtube-transcript-api)."
        ),
    ),
    player_clients: Optional[str] = typer.Option(
        None, "--player-clients",
        help=(
            "Comma-separated yt-dlp player clients to rotate "
            "(default: android,android_vr,tv_simply,tv_embedded,mweb,web,ios). "
            "Only used when --backend=ytdlp or auto."
        ),
    ),
    backend_order: Optional[str] = typer.Option(
        None, "--backend-order",
        help=(
            "When --backend=auto, comma-separated cascade order. "
            "Default: 'ytdlp,watch,api'. Example: 'watch,ytdlp,api'."
        ),
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
        cookies_file=cookies,
        user_agent=user_agent,
        verbose=verbose,
    )
    backend = (backend or "auto").lower().strip()
    if backend not in {"auto", "watch", "ytdlp", "api"}:
        console.print(
            f"[red]Unknown backend {backend!r}. "
            "Use 'auto' | 'watch' | 'ytdlp' | 'api'.[/red]"
        )
        raise typer.Exit(code=2)

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

    ckpt: Optional[Checkpoint] = None
    if cfg.resume:
        ckpt_path = cfg.checkpoint_file or (cfg.output_dir / ".yttp-checkpoint.json")
        ckpt = Checkpoint(ckpt_path)
        before = len(videos)
        videos = [v for v in videos if not ckpt.is_done(v.video_id)]
        skipped = before - len(videos)
        if skipped:
            console.print(f"[yellow]Resume: skipping {skipped} already completed.[/yellow]")

    clients = (
        [c.strip() for c in player_clients.split(",") if c.strip()]
        if player_clients
        else None
    )
    if backend == "auto":
        order = (
            [b.strip() for b in backend_order.split(",") if b.strip()]
            if backend_order
            else None
        )
        extractor: Any = AutoTranscriptExtractor(cfg, backend_order=order)
        order_str = "→".join(order) if order else "ytdlp→watch→api"
        console.print(
            f"[dim]Backend: auto ({order_str} cascade + per-backend circuit breaker).[/dim]"
        )
    elif backend == "watch":
        extractor = WatchPageTranscriptExtractor(cfg)
        console.print(
            "[dim]Backend: watch-page scraper (bypasses Innertube blocks).[/dim]"
        )
    elif backend == "ytdlp":
        extractor = YtDlpTranscriptExtractor(cfg, player_clients=clients)
        console.print(
            "[dim]Backend: yt-dlp (multi-client rotation).[/dim]"
        )
    else:
        extractor = TranscriptExtractor(cfg)
        console.print("[dim]Backend: youtube-transcript-api.[/dim]")
    writer = FormatWriter(cfg)
    results = _run(extractor, writer, videos, cfg, ckpt, combined_name=combined_name)

    ok = sum(1 for r in results if r.success)
    fail = len(results) - ok
    console.rule("[bold]Summary")
    console.print(f"[green]Success:[/green] {ok}    [red]Failed:[/red] {fail}")
    if cfg.combine_into_single_file:
        formats = _formats_to_write(cfg.output_format)
        for f in formats:
            path = cfg.output_dir / f"{combined_name}.{f}"
            console.print(f"[cyan]Combined → {path}[/cyan]")


def _formats_to_write(fmt: str) -> list[str]:
    if fmt == "all":
        return ["txt", "json", "srt", "vtt", "md", "csv"]
    return [fmt]


def _run(
    extractor: Any,
    writer: FormatWriter,
    videos: list[VideoMetadata],
    cfg: Config,
    ckpt: Optional[Checkpoint],
    *,
    combined_name: str = "combined",
) -> list[TranscriptResult]:
    formats = _formats_to_write(cfg.output_format)
    results: list[TranscriptResult] = []

    # Detect non-TTY (nohup / piped) so Rich's transient progress bar
    # can still print status lines to the log instead of silently
    # updating a single invisible line.
    is_tty = sys.stderr.isatty() and sys.stdout.isatty()

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
        disable=not is_tty,
    ) as progress:
        task_id = progress.add_task("Extracting", total=len(videos))

        def on_progress(done: int, total: int, res: TranscriptResult) -> None:
            # Use ASCII on Windows legacy consoles to avoid cp1252 crashes;
            # the top-of-file workaround already reconfigures stdout to UTF-8,
            # but this belt-and-braces keeps the progress bar safe even if
            # the user pipes output to a file with a narrow encoding.
            if os.name == "nt":
                status = "OK" if res.success else "XX"
            else:  # pragma: no cover — Unix only
                status = "✓" if res.success else "✗"
            progress.update(
                task_id,
                completed=done,
                description=f"[{status}] {res.metadata.video_id}",
            )
            # Non-TTY fallback — still emit a line per completed video so
            # nohup / CI logs are informative.
            if not is_tty and (done % 5 == 0 or done == total or not res.success):
                logging.info(
                    "[%s] %d/%d %s%s",
                    status,
                    done,
                    total,
                    res.metadata.video_id,
                    "" if res.success else f"  ERR: {(res.error or '')[:120]}",
                )
            if res.success:
                for f in formats:
                    try:
                        if cfg.combine_into_single_file:
                            writer.append_combined(res, f, filename=combined_name)
                        else:
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
    max_videos: Optional[int] = typer.Option(None, "--max-videos", "-n"),
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
