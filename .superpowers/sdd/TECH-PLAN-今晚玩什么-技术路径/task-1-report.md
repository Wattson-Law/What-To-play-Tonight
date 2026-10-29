# Task 1 report

Status: complete

Files changed: pyproject.toml, src/coop_night_picker/__init__.py, src/coop_night_picker/probe.py, scripts/probe_network.py, .env.example, README.md, tests/test_probe.py.

Tests run and output: `PYTHONPATH=src python -m unittest discover -s tests -v` — 3 tests passed.

Concerns: Steam may block appreviews; README documents fallback to store HTML or an offline snapshot. Live probe behavior depends on network access.

## Fix round 1

Commit `9701d5b`: strict mode now treats only 2xx responses as successful, with offline tests covering redirects and HTTP errors. Generated egg-info artifacts were removed and ignored.
