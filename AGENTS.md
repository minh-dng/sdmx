# Repository Guidelines

## Project structure and module organization

The `sdmx/` package contains the public client, SDMX models, readers, writers, format definitions, and provider-specific adapters in `sdmx/source/`. Keep shared test helpers in `sdmx/testing/`; place tests in `sdmx/tests/`, mirroring the package layout with subdirectories such as `reader/`, `writer/`, and `model/`. Sphinx documentation lives in `doc/`. Small source fixtures used by tests belong in `source-tests/`; larger SDMX specimens come from the separate `sdmx-test-data` repository.

## Build, test, and development commands

- `uv pip install --editable '.[cache,docs,tests]'` installs the package and development extras in the active environment.
- `uvx pre-commit run --all-files --show-diff-on-failure` runs Ruff formatting and lint checks, then mypy. Run this before tests.
- `uv run --no-sync pytest --sdmx-fetch-data` runs the default suite and fetches external specimens into the user cache. Pytest excludes `experimental` and `source` tests by default.
- `uv run --no-sync pytest -m source` runs slow, network-dependent provider tests.
- `make -C doc html` builds the Sphinx documentation in `doc/_build/html`.
- `uv build` creates source and wheel distributions with Hatchling.

## Commit & Pull Request Guidelines

Use `conventional-commit` for both commits and PR titles. Do small trackable commits. PRs should state the target/configuration, commands run, output changes, and linked issue; add logs or screenshots only when useful.

## Testing guidelines

Use pytest and name files `test_*.py` and functions `test_*`. Add the smallest test that demonstrates behavior static analysis cannot verify; do not add tests for errors Ruff or mypy already catches. Coverage is collected for `sdmx`, but the repository sets no fixed percentage threshold. Mark live-network tests with `network` or `source` as appropriate, and prefer recorded responses for deterministic tests.

## Agent skills

### Issue tracker

Issues are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the default five canonical triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout. See `docs/agents/domain.md`.
