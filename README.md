# yt-transcript-pro

[![CI](https://github.com/Avinashvelu03/yt-transcript-pro/actions/workflows/ci.yml/badge.svg)](https://github.com/Avinashvelu03/yt-transcript-pro/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](#)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12-blue.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![Security: bandit](https://img.shields.io/badge/security-bandit-green.svg)](https://github.com/PyCQA/bandit)

**The most advanced, production-grade YouTube transcript extractor.**
Single videos, multi-video batches, full playlists, or entire channels — with
concurrency, retries, checkpointing, and six output formats. Zero lint warnings,
zero security issues, **100% test coverage**.

## ✨ Why this one

| Feature | `yt-transcript-pro` | Typical alternatives |
|---|---|---|
| Single video, playlist, **and entire channel** | ✅ | Partial |
| Async concurrent fetching (configurable) | ✅ | Rare |
| Exponential-backoff retries (tenacity) | ✅ | Rare |
| Checkpointing / resume after interruption | ✅ | ❌ |
| 6 output formats (TXT/JSON/SRT/VTT/MD/CSV) + `all` | ✅ | 1–2 |
| Combined single-file output | ✅ | ❌ |
| Optional timestamps in plain-text output | ✅ | Sometimes |
| Rich CLI with live progress bar | ✅ | ❌ |
| Strict typing (mypy strict, py.typed) | ✅ | ❌ |
| 100% branch coverage test suite | ✅ | ❌ |
| Zero bandit / ruff / mypy findings | ✅ | ❌ |

## 🚀 Install

```bash
pip install yt-transcript-pro
# or from source:
git clone https://github.com/Avinashvelu03/yt-transcript-pro
cd yt-transcript-pro
pip install -e ".[dev]"
```

## ⚡ Quickstart

```bash
# Single video
yttp extract dQw4w9WgXcQ -o ./out

# Multiple videos, combined into one text file
yttp extract VID1 VID2 VID3 -o ./out -f txt --combine

# Entire playlist → SRT subtitles
yttp extract "https://www.youtube.com/playlist?list=PLabc" -f srt -o ./out

# Entire channel → markdown + JSON + TXT, 10 parallel workers
yttp extract "https://www.youtube.com/@SomeHandle" -f all -c 10 -o ./out

# From a file of URLs/IDs (one per line; # for comments)
yttp extract urls.txt -o ./out

# Preview only (no download)
yttp resolve "https://www.youtube.com/c/SomeChannel" -n 50
```

Full options:

```bash
yttp extract --help
```

## 🐍 Python API

```python
import asyncio
from yt_transcript_pro import (
    Config, SourceResolver, TranscriptExtractor, FormatWriter,
)

async def main() -> None:
    cfg = Config(concurrency=8, output_format="txt", combine_into_single_file=True)
    videos = SourceResolver().resolve(["https://www.youtube.com/@SomeHandle"])
    results = await TranscriptExtractor(cfg).fetch_many(videos)
    FormatWriter(cfg).write_combined(results, "txt", "all_transcripts")

asyncio.run(main())
```

## 🧪 Development

```bash
pip install -e ".[dev]"
pytest                     # run tests
pytest --cov               # with coverage (must be 100%)
ruff check src tests       # lint
mypy src                   # strict type-check
bandit -c pyproject.toml -r src   # security scan
```

## 🛡️ Working around YouTube IP bans

YouTube blocks transcript requests from most cloud-provider IPs (AWS, GCP, Azure,
sandbox/CI environments). If you see `IpBlocked` / `RequestBlocked` errors, you
need to route through a proxy. `yt-transcript-pro` supports two approaches:

### Option 1: Generic HTTP(S) proxy

```bash
yttp extract "https://www.youtube.com/@SomeHandle" \
  --proxy http://user:pass@proxy.example.com:8080 \
  -o ./out --combine
```

### Option 2: Webshare rotating-residential proxies (recommended)

Sign up at <https://www.webshare.io/> and grab your credentials:

```bash
yttp extract "https://www.youtube.com/@SomeHandle" \
  --webshare-user YOUR_USER --webshare-pass YOUR_PASS \
  -o ./out --combine
```

Both flags are also available via the Python API through the `Config` object
(`proxy=`, `webshare_proxy_username=`, `webshare_proxy_password=`).

## 🐳 Docker

```bash
docker build -t yt-transcript-pro .
docker run --rm -v "$PWD/out:/app/out" yt-transcript-pro \
    extract "https://www.youtube.com/@SomeHandle" -o /app/out -f txt --combine
```

## 📄 License

MIT © [Avinashvelu03](https://github.com/Avinashvelu03)

## 🙏 Credits

Built on top of [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api)
and [`yt-dlp`](https://github.com/yt-dlp/yt-dlp). Thank you to those maintainers.
