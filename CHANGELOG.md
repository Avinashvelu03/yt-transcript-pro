# Changelog

## v2.0.0 — 2026-04-23

**Breaking, feature, and bug-fix release — fully backward-compatible at the
Python API level.**

### ✨ New: IP-block-resistant backends
* **`AutoTranscriptExtractor`** — new extractor that cascades over
  `ytdlp → watch → api` per video, with a per-backend circuit
  breaker. Default for the CLI (`--backend auto`).
* **`WatchPageTranscriptExtractor`** — scrapes `/watch?v=…` HTML for
  `ytInitialPlayerResponse` and parses captions from there. Bypasses
  Innertube's aggressive anti-bot.
* **`YtDlpTranscriptExtractor`** — uses yt-dlp's player API with
  automatic client rotation
  (`android → android_vr → tv_simply → tv_embedded → mweb → web → ios`).
  Zero-proxy IP-block resistance: ``android`` & ``android_vr`` have
  independent quotas and work on cloud IPs that `youtube-transcript-api`
  gets blocked on within minutes.

### 🔧 New CLI flags
* `-b/--backend` — `auto` (default) | `watch` | `ytdlp` | `api`
* `--backend-order` — override the cascade order (e.g. `watch,ytdlp,api`)
* `--player-clients` — override yt-dlp client rotation order
* `--cookies` — Netscape `cookies.txt` file for age-restricted / private
  videos
* `--user-agent` — override the rotating User-Agent

### 🐛 Bug fixes
* **Windows Unicode crash** — on Windows consoles the `✓`/`✗` progress
  glyphs used by Rich caused `UnicodeEncodeError: 'charmap' codec can't
  encode character '\\u2717'`. Fixed by forcing UTF-8 on stdout/stderr
  and falling back to ASCII `OK`/`XX` in the progress bar.
* **Checkpoint skip** with `--resume` now correctly skips already-completed
  videos when using any backend.
* **Combined-file writes are now incremental** — previously the CLI held
  all transcripts in memory and wrote once at the end, losing all work on
  Ctrl-C. Now each transcript is appended atomically as it completes.
* **Non-TTY / `nohup` progress** is now visible in log files (Rich
  previously suppressed all output when stdout wasn't a TTY).
* **Adaptive throttling** now caps per-worker backoff at 15s (was 60s),
  and noisy "strike N" log lines are emitted only every 5 strikes so
  logs stay readable during long runs.

### 🧪 Tests
* +18 new unit tests for the ytdlp / watch / auto backends
* Total: **131 tests, all passing** — no network access required.

---

## v1.0.0 — 2025-05-XX

Initial release: single videos, playlists, channels, 6 output formats,
concurrency, retries, checkpointing, 100% coverage. Uses
`youtube-transcript-api` as the single transcript backend.
