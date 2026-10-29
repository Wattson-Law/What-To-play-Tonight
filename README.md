# coop-night-picker

Requires Python 3.11 or newer. Install with `python -m pip install -e .`.

Run `python scripts/probe_network.py` for direct probes. Add `--proxy` to run proxy probes using `STEAM_PROXY` (or `http://127.0.0.1:7890`); pass a URL after the flag to override it. Add `--strict` to return a failure code when a complete combination fails. Results include JSON and a readable table, and redirects are never followed.

The `appreviews` endpoint may be blocked by Steam or an intermediary. Review data therefore falls back to Steam store HTML or an offline snapshot.
