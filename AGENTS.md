# open-fpl-solver

FPL squad optimization via HiGHS (`highspy`) + pandas. Python 3.14+, `uv` managed. Core: `dev/solver.py` (model build), entrypoint: `run/solve.py`. Settings in `data/user_settings.json` / `data/comprehensive_settings.json`.

## Dev environment
- `uv sync` (installs `highspy>=1.11.0`, pandas, numpy, ruff, pytest; needs Python >=3.14 via uv)
- `datasource` in `data/user_settings.json` picks projection CSV (e.g., `solio.csv` in `data/`)

## Build & test (verified from repo)
- `uv run python run/solve.py`
- `uv run ruff check .` / `uv run ruff format --line-length 150 .`
- `uv run pytest` (only `tests/test_options_parsing.py`; `tests/` ignores `PLR2004`)
- `.pre-commit-config.yaml`: `ruff-format --line-length=150` + trailing-whitespace / check-json / mixed-line-ending

## Conventions (observed, not assumed)
- Line length 150; ruff target `py313` (`pyproject.toml`).
- First-party imports: `src`, `run`, `tests` (`known-first-party`).
- `run/solve.py` and `dev/solver.py` disable `PLR0915`/`PLR0912` per-file.
- `tests/__init__.py` ignores `F401`.
- HiGHS binary variables = `highspy.HighsVarType.kInteger` bounded [0,1] (`BIN`); `BINARY_THRESHOLD = 0.5` in both modules.

## Pitfalls
- `highspy>=1.11.0` required — don't downgrade.
- `data/results/` and `run/tmp/` contain generated outputs (only `.gitkeep`); don't hand-edit fixtures.
- Format with `--line-length=150` to match `.pre-commit-config.yaml`.
- `run/solve.py` has a commented `subprocess` git-fetch version check (`is_latest_version`); it can error offline — ignore if no network.
