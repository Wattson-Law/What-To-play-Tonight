# Curator-Driven Cloud Snapshot & Live Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `coop-night-picker` from raw Steam search scraping to a Steam Curator-driven ("aggregating the aggregators") cloud pipeline that runs automatically on GitHub Actions, extracts human curator blurbs + player counts + gameplay tags (including Indie & new Demos), and lets the MCP server read the cloud snapshot or fall back to live Steam Curator queries without requiring local snapshot generation.

**Architecture:** `SteamClient` fetches recommendations from curated Steam Curators (`curator/<clanid>/ajaxgetfilteredrecommendations`) and top Chinese curators (`curators/ajaxgetcurators`). `extractor.py` parses curator attitude (`recommended`/`informational`/`not_recommended`), human review blurbs, tag IDs, and player limits (`max_players`). `db.py` migrates `games` idempotently (`curator_count`, `curators_json`), supports tag/genre filtering (`tag`), and ranks curator-backed games first. `server.py` auto-fetches the latest GitHub Actions snapshot or falls back to live curator queries when no local DB is present, and `.github/workflows/update_snapshot.yml` triggers automatically on push to `main` or `implementation`.

**Tech Stack:** Python 3.11+, `sqlite3`, `httpx` / `urllib.request`, `beautifulsoup4`, `mcp` (`FastMCP`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-02-curator-driven-recommendations-design.md`

## Global Constraints

- Python `>=3.11` compatibility; offline unit tests must not require live network access.
- Do not generate or write `data/games.sqlite` on the local development machine; snapshot generation belongs to GitHub Actions (`update_snapshot.yml`).
- Curator quotes stored in `game_quotes` must preserve the curator name prefix `[鉴赏家·<name>]` and a valid Steam link (`https://store.steampowered.com/app/<appid>/?curator_clanid=<clanid>` or `https://steamcommunity.com/...`).

## Review Focus

- Existing SQLite databases lacking `curator_count` or `curators_json` columns must migrate idempotently in `init_db` without losing data.
- New Demos with `review_score == 0` (`评测不足/试玩区`) that have `curator_count > 0` must be surfaced with their curator blurbs instead of having empty reasons.
- `tag` filtering (e.g. `tag="独立"` or `tag="indie"` or `tag="生存"`) must match both Chinese and English aliases against `tags_json` and `categories_json`.
- `max_players` extraction must infer numeric player counts from common Chinese/English descriptions (`四人`, `双人`, `2-4人`, `up to 4 players`, `online co-op`) with safe defaults when unspecified.
- When `DB_PATH` is absent locally, `server.py` must attempt cloud snapshot download / live curator fallback rather than failing immediately if a remote source is configured or mocked.

---

### Task 1: Curator Sources, Tag Aliases & SteamClient Curator Endpoints

**Files:**
- Modify: `src/coop_night_picker/constants.py`
- Modify: `src/coop_night_picker/steam_client.py`
- Test: `tests/test_curators.py`

**Interfaces:**
- Produces:
  - `CURATOR_SOURCES: list[dict[str, Any]]` in `constants.py` (`[{"clanid": 6859276, "name": "Co-Optimus", "focus": "coop"}, ...]`)
  - `TAG_ALIASES: dict[str, list[str]]` in `constants.py` (maps `"indie"`/`"独立"` -> `["独立", "Indie", "492"]`, etc.)
  - `SteamClient.curator_recommendations(clanid: int, tagids: str = "3843", start: int = 0, count: int = 50) -> dict`
  - `SteamClient.top_curators(start: int = 0, count: int = 20) -> dict`

- [ ] **Step 1: Write the failing test in `tests/test_curators.py` for `CURATOR_SOURCES` and `SteamClient.curator_recommendations`**
- [ ] **Step 2: Run `pytest tests/test_curators.py` to verify failure**
- [ ] **Step 3: Implement `CURATOR_SOURCES`, `TAG_ALIASES`, `curator_recommendations`, and `top_curators`**
- [ ] **Step 4: Run `pytest tests/test_curators.py` to verify pass**

---

### Task 2: Curator HTML Extractor, `max_players` Inference & Schema Migration

**Files:**
- Modify: `src/coop_night_picker/extractor.py`
- Modify: `src/coop_night_picker/db.py`
- Modify: `scripts/verify_snapshot.py`
- Test: `tests/test_curators.py`

**Interfaces:**
- Consumes: `CURATOR_SOURCES`, `TAG_ALIASES`, `TAG_ZH` from `constants.py`
- Produces:
  - `parse_curator_recommendations(payload_or_html: dict | str, curator_name: str = "Steam鉴赏家", clanid: int = 0) -> list[dict]`
  - `infer_max_players(text: str, categories: list[str] | None = None, tags: list[str] | None = None) -> int | None`
  - `db.init_db` idempotent `ALTER TABLE games ADD COLUMN curator_count INTEGER DEFAULT 0` and `curators_json TEXT`
  - `db.recommend_games(conn, *, horror='any', budget='any', players=None, mode='online', tag=None, session_duration=None, limit=50)` ordered by `curator_count DESC, review_score DESC, positive_pct DESC`

- [ ] **Step 1: Add failing extractor and DB tests in `tests/test_curators.py`**
- [ ] **Step 2: Run `pytest tests/test_curators.py` to verify failure**
- [ ] **Step 3: Implement `parse_curator_recommendations`, `infer_max_players`, DB schema migration, and `tag`/`curator_count` query support**
- [ ] **Step 4: Update `scripts/verify_snapshot.py` to accept both `https://steamcommunity.com/profiles/` and `https://store.steampowered.com/app/` curator review URLs**
- [ ] **Step 5: Run `pytest` to verify pass**

---

### Task 3: Curator-First Snapshot Builder, Cloud/Live Server Fallback & GitHub Actions Auto-Trigger

**Files:**
- Modify: `scripts/build_snapshot.py`
- Modify: `src/coop_night_picker/server.py`
- Modify: `.github/workflows/update_snapshot.yml`
- Modify: `README.md`
- Test: `tests/test_curators.py`

**Interfaces:**
- Consumes: `SteamClient.curator_recommendations`, `parse_curator_recommendations`, `infer_max_players`, `db.recommend_games(..., tag=...)`
- Produces:
  - `build_snapshot.py` pulls from `CURATOR_SOURCES` first, aggregates `curator_count`, `curators_json`, and stores curator blurbs into `game_quotes` (positive for `recommended`/`informational`, negative + `avoid_reasons_json` for `not_recommended`), then enriches via store/appdetails/appreviews.
  - `server.recommend_coop_games(..., tag: Optional[str] = None)` filters by tag, displays `🏅 鉴赏家背书`, and supports cloud snapshot download (`COOP_NIGHT_PICKER_CLOUD_DB_URL`) or live curator fallback when local DB is absent.
  - `.github/workflows/update_snapshot.yml` triggers on `push` (branches `main`, `implementation`), `schedule`, and `workflow_dispatch`.

- [ ] **Step 1: Add failing tests for curator snapshot building and `server.recommend_coop_games(tag=...)` in `tests/test_curators.py`**
- [ ] **Step 2: Implement updates in `scripts/build_snapshot.py`, `src/coop_night_picker/server.py`, and `.github/workflows/update_snapshot.yml`**
- [ ] **Step 3: Remove any locally generated `data/games.sqlite` from the working tree if needed or let GitHub Actions regenerate it in the cloud, run full `pytest` and `unittest` suites**
- [ ] **Step 4: Commit and push to `origin` (`main` and `implementation`) and verify GitHub Actions workflow run triggers**
