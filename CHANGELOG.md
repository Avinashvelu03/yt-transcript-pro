# Changelog

## [1.0.0] — 2026-04-21

### Added
- Initial production release.
- `yttp extract` / `yttp resolve` Typer-based CLI.
- Source resolver handling single videos, playlists, entire channels, and text files.
- Async concurrent `TranscriptExtractor` with tenacity-powered exponential backoff.
- Six output formats: `txt`, `json`, `srt`, `vtt`, `md`, `csv` (plus `all`).
- Combined-file output for consolidating many videos into one file.
- Checkpointing for resuming interrupted runs.
- Dockerfile, GitHub Actions CI matrix (Py 3.9–3.12), pre-commit hooks.
- 100% branch coverage test suite (110 tests).
- Zero findings in `ruff`, `mypy --strict`, and `bandit`.
