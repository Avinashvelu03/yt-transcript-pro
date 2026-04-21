# Contributing

Thanks for considering a contribution! A few ground rules:

1. **Quality gates must stay green.** Every PR must keep:
   - `ruff check src tests` clean
   - `mypy src` clean (strict mode)
   - `bandit -c pyproject.toml -r src` clean
   - `pytest --cov` at **100%** coverage
2. Use [conventional commits](https://www.conventionalcommits.org/) if you can.
3. Add/adjust tests for every code change.
4. Install the pre-commit hooks:

```bash
pip install pre-commit
pre-commit install
```

## Dev setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
