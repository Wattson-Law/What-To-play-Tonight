# Task 4 report

- **Status:** complete
- **Date:** 2026-10-29
- **Branch:** implementation

## Files changed

- `.github/workflows/update_snapshot.yml`: weekly/manual snapshot workflow, Python 3.12 setup, dependency install, build/verify, commit only changed `data/games.sqlite`.
- `README.md`: project layout, installation, module run command, absolute `PYTHONPATH` MCP configuration, snapshot/offline commands, probe command, source limitations, four tools and prompt.
- `.gitignore`: user log, HTTP/cache folders, fixture/temp folders, Python/build artifacts; keeps `data/games.sqlite` eligible for Actions commits.
- `tests/test_acceptance.py`: temporary SQLite/user-log offline acceptance coverage for need-more-info, filtering/grouping, avoid evidence, log exclusion, local resolve, Markdown, and optional FastMCP registration.
- `docs/ACCEPTANCE.md`: exact commands and observed outputs.

## Verification

- `PYTHONPATH=src pytest -q` -> `10 passed, 1 skipped`.
- `python scripts/verify_snapshot.py --db tmp-fixtures/valid.sqlite --allow-empty` -> exit `0`, `games=1 quotes=1 tiers={9: 1}` (the one-game fixture uses the documented local smoke-test bypass).
- `python scripts/verify_snapshot.py --db tmp-fixtures/missing.sqlite` -> exit `1`, `missing database`; no file was created or modified.
- `python scripts/probe_network.py --json` -> exit `0`; direct official probes were connection errors, CDN probes included one `ok` and redirect traps.

## Concerns

- Steam availability is environment-dependent. `appreviews` may be blocked; the builder and server treat absent review payloads as empty evidence and continue with the snapshot.
- FastMCP is optional for pure offline tool logic; this environment exposes the `mcp` package name but not an importable `FastMCP`, so only that registration assertion is skipped.

## Fix round 1 (2026-10-29)

- Workflow now stages `data/games.sqlite` before checking `git diff --staged --quiet`, covering both new and changed snapshots without empty commits.
- Acceptance tests now assert the avoid list, scary-game exclusion under `horror="none"`, and negative review evidence attribution.
- Added `scripts/create_offline_fixture.py`; README and acceptance commands now explain how to create the ignored temporary fixture directory.
- Re-ran `pytest -q`: `10 passed, 1 skipped`; fixture verification succeeded; PyYAML load reported `yaml ok`.
- Pinned `mcp[cli]>=1.9,<2` for the supported FastMCP import path.
- Snapshot builds now replace prior quotes per appid before inserting refreshed evidence; normal verification enforces the plan's 50-game/sample-distribution minimum, with `--allow-empty` reserved for local smoke tests.
- Fix round 1 files include `pyproject.toml`, `scripts/build_snapshot.py`, `scripts/verify_snapshot.py`, `scripts/create_offline_fixture.py`, workflow, README, `.gitignore`, acceptance tests, acceptance docs, and this report.
- The offline fixture generator now removes an existing output database before recreation, so repeated runs remain `games=1 quotes=1 tiers={9: 1}`.
