# yt-transcript-pro v2.0

[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 130 passing](https://img.shields.io/badge/tests-130%20passing-brightgreen.svg)](#)

**The most advanced, production-grade YouTube transcript extractor.**
Single videos, multi-video batches, full playlists, or entire channels —
with concurrency, retries, checkpointing, six output formats, and
— new in v2 — **four interchangeable extraction backends with automatic
cascade fallback that bypass IP blocks without needing proxies**.

---

## 🚀 What's new in v2.0

The old v1 used `youtube-transcript-api` exclusively, which hits
YouTube's `/api/timedtext` endpoint directly. That endpoint is the first
thing YouTube rate-limits — after ~250 rapid requests the IP gets
`RequestBlocked` / `IpBlocked` errors for 1–24 hours.

**v2 ships four independent backends** that use completely different
endpoints, and an **`auto` backend** that cascades through them per
video:

| Backend | Endpoint | Resilience | Speed |
|---|---|---|---|
| **`auto`** (default) | cascades `watch → ytdlp → api` | ⭐⭐⭐⭐⭐ | fast |
| **`watch`** | `GET /watch?v=<id>` HTML scrape | ⭐⭐⭐⭐ | fastest |
| **`ytdlp`** | yt-dlp player API with 7-client rotation | ⭐⭐⭐⭐ | fast |
| **`api`** (legacy v1) | `youtube-transcript-api` | ⭐⭐ | fast |

**Why this works without proxies**: each backend hits a different
YouTube surface with different rate-limit heuristics. When one backend
is throttled, the cascade automatically switches to the next — and each
one has its own independent adaptive back-off (rogues of rapid requests
trigger per-backend slowdowns so other backends keep flowing).

Empirically verified on **cloud IPs that `youtube-transcript-api` gets
blocked on within seconds** — v2 keeps producing transcripts.

### Other v2 improvements
- 🛡️  **Windows Unicode fix** – no more `cp1252` crashes on the ✓/✗ progress glyphs.
- 📝  **Incremental combined-file writes** – partial runs produce durable output; Ctrl-C doesn't lose data.
- 🔄  **Per-backend adaptive throttling** – cooperative sleeps slow *just* the backend being throttled, not the whole pool.
- 🎯  **7-client rotation for `ytdlp`**: `android → android_vr → tv_simply → tv_embedded → mweb → web → ios`. Each client has its own rate-limit pool and user-agent fingerprint.
- 🍪  **Cookies.txt support** for age-restricted / private videos (`--cookies cookies.txt`).
- 🕵️  **Rotating modern User-Agents** (Chrome, Firefox, Safari, Edge, Android Chrome).
- 📊  **Non-TTY / `nohup` logging** – progress is still visible in log files.
- 🧪  **130 unit tests**, zero network access required to run the suite.

---

## ⚡ Install

```bash
# Clone / unzip and install
cd yt-transcript-pro
pip install -e ".[dev]"
```

**Requires Python ≥ 3.9**. Dependencies: `yt-dlp`, `youtube-transcript-api`, `pydantic`, `tenacity`, `typer`, `rich`.

---

## 📖 Quickstart

### Entire channel → one combined text file

```bash
# This is the exact command that extracts all 665 InnerCircleTrader videos:
yttp extract "https://www.youtube.com/@InnerCircleTrader" \
  --output-dir ./channel_extraction/ICT \
  --format txt \
  --combine \
  --combined-name InnerCircleTrader_all_transcripts \
  --concurrency 5 \
  --retries 5 \
  --resume
```

- `--backend` defaults to `auto` — cascades `watch → ytdlp → api`.
- `--resume` skips videos already completed (re-run without re-downloading).
- `--concurrency 5` is a safe default. Bump to `10` for residential IPs;
  drop to `2` for cloud IPs.

### Single video

```bash
yttp extract dQw4w9WgXcQ -o ./out
yttp extract "https://youtu.be/dQw4w9WgXcQ" -o ./out
```

### Playlist

```bash
yttp extract "https://www.youtube.com/playlist?list=PLxxxx" -o ./out -f srt
```

### All channel playlists -> one playlist-organized text file

```bash
python extract_playlists.py
```

The playlist runner writes one consolidated file plus an index, report,
and failure checkpoint under `channel_extraction/ICT_playlists/`. It
reuses transcript blocks already present in
`../InnerCircleTrader_all_transcripts.txt` and caches each newly fetched
video under `channel_extraction/ICT_playlists/transcripts/` for clean
resume behavior.

### Batch from a file of URLs/IDs

```bash
cat > urls.txt <<EOF
# one URL or ID per line; # for comments
https://www.youtube.com/watch?v=AAAA
dQw4w9WgXcQ
https://youtu.be/XXXX
EOF

yttp extract urls.txt -o ./out --combine
```

---

## 🧰 Full CLI reference

```bash
yttp extract --help
```

Key flags:

| Flag | Default | Description |
|---|---|---|
| `-o/--output-dir` | `output/` | Where to write files |
| `-f/--format` | `txt` | `txt`\|`json`\|`srt`\|`vtt`\|`md`\|`csv`\|`all` |
| `-C/--combine` | off | Combine all transcripts into one file |
| `--combined-name` | `combined` | Filename stem for combined output |
| `-c/--concurrency` | `5` | Parallel fetchers (1-64) |
| `-n/--max-videos` | unlimited | Cap total videos processed |
| `-l/--languages` | `en,en-US,en-GB` | Preferred language list |
| `--timestamps` | off | Prefix each line with `[HH:MM:SS]` |
| `--allow-generated` | on | Fall back to auto-captions |
| `--resume` | on | Skip already-completed videos |
| `--checkpoint` | `<out>/.yttp-checkpoint.json` | Checkpoint location |
| `--retries` | `4` | Max per-video retries on transient errors |
| **`-b/--backend`** | **`auto`** | **`auto` \| `watch` \| `ytdlp` \| `api`** |
| `--player-clients` | (built-in) | Override yt-dlp client order |
| `--cookies` | none | Netscape `cookies.txt` (age-restricted videos) |
| `--user-agent` | rotating | Fixed HTTP User-Agent |
| `--proxy` | none | `http://user:pass@host:port` |
| `--webshare-user/-pass` | none | Webshare rotating residential proxy |
| `-v/--verbose` | off | Debug logging |

### Windows users

If you were hitting `UnicodeEncodeError: 'charmap' codec can't encode
character '\\u2717'` in v1 — **that's fixed in v2**. The CLI now forces
UTF-8 output on Windows.

If you still see it (exotic terminal setup), set:
```powershell
$env:PYTHONIOENCODING="utf-8"
chcp 65001
```

---

## 🐍 Python API

```python
import asyncio
from yt_transcript_pro import (
    Config,
    SourceResolver,
    AutoTranscriptExtractor,   # ← new in v2, recommended
    FormatWriter,
)

async def main() -> None:
    cfg = Config(
        concurrency=5,
        output_format="txt",
        combine_into_single_file=True,
        output_dir=Path("out"),
    )
    videos = SourceResolver().resolve(["https://www.youtube.com/@InnerCircleTrader"])
    ext = AutoTranscriptExtractor(cfg)
    results = await ext.fetch_many(videos)

    writer = FormatWriter(cfg)
    for r in results:
        if r.success:
            writer.append_combined(r, "txt", filename="all")

asyncio.run(main())
```

You can also use any backend directly:

```python
from yt_transcript_pro import YtDlpTranscriptExtractor, WatchPageTranscriptExtractor

watch = WatchPageTranscriptExtractor(cfg)          # scrape /watch HTML
ydl   = YtDlpTranscriptExtractor(cfg)              # yt-dlp player API
ydl   = YtDlpTranscriptExtractor(
    cfg,
    player_clients=["android_vr", "tv_simply"],    # custom client order
)
```

---

## 🛡️ Troubleshooting IP blocks

If you still get blocks (very rare with `auto` backend):

1. **Wait out the cool-down.** YouTube typically unblocks IPs after
   1–24 hours. Your progress is saved in `.yttp-checkpoint.json` —
   just re-run with `--resume` and it picks up where it left off.

2. **Drop concurrency** to 2 or 1. The adaptive throttler will further
   slow down automatically, but a lower starting concurrency is gentler
   on heavily-flagged IPs.

3. **Export your browser cookies** (Netscape format) and pass
   `--cookies cookies.txt`. Authenticated requests have a **much
   higher** rate-limit ceiling.

4. **Use a proxy** as a last resort (`--proxy` or `--webshare-user/-pass`).
   Residential proxies work best; datacenter proxies are often
   pre-blocked.

---

## 🧪 Running the tests

```bash
pip install -e ".[dev]"
pytest -v          # 130 tests, no network, < 1s
```

---

## 📦 Project layout

```
src/yt_transcript_pro/
├── __init__.py
├── auto_extractor.py        # ← new: cascade over all backends
├── checkpoint.py
├── cli.py                   # ← updated: --backend flag
├── config.py                # ← updated: cookies_file, user_agent
├── extractor.py             # legacy youtube-transcript-api backend
├── models.py
├── resolver.py              # channel/playlist/video URL resolution
├── watch_extractor.py       # ← new: /watch HTML scraper
├── writers.py               # ← updated: append_combined()
└── ytdlp_extractor.py       # ← new: yt-dlp backend w/ 7-client rotation
tests/                       # 130 unit tests
```

---

## 📝 License

MIT (same as v1). See `LICENSE`.

---

## 🙏 Credits

Built on top of the excellent [yt-dlp](https://github.com/yt-dlp/yt-dlp),
[youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api),
[typer](https://typer.tiangolo.com/), [rich](https://rich.readthedocs.io/),
and [pydantic](https://pydantic.dev/).
