from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .constants import TAG_ALIASES

SCHEMA = """CREATE TABLE IF NOT EXISTS games (
    appid INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT DEFAULT 'game',
    is_free INTEGER DEFAULT 0,
    has_demo INTEGER DEFAULT 0,
    demo_appid INTEGER,
    fullgame_appid INTEGER,
    price_cny REAL DEFAULT 0.0,
    discount_pct INTEGER DEFAULT 0,
    online_coop INTEGER DEFAULT 0,
    local_coop INTEGER DEFAULT 0,
    remote_play INTEGER DEFAULT 0,
    max_players INTEGER,
    horror_level TEXT DEFAULT 'none',
    review_score INTEGER DEFAULT 0,
    review_tier_zh TEXT DEFAULT '评测不足',
    total_reviews INTEGER DEFAULT 0,
    positive_pct REAL DEFAULT 0.0,
    schinese_score INTEGER,
    schinese_pct REAL,
    schinese_total INTEGER,
    short_desc TEXT,
    short_desc_zh TEXT,
    categories_json TEXT,
    tags_json TEXT,
    avoid_reasons_json TEXT,
    is_avoid INTEGER DEFAULT 0,
    header_image TEXT,
    updated_at TEXT,
    curator_count INTEGER DEFAULT 0,
    curators_json TEXT,
    coming_soon INTEGER DEFAULT 0,
    release_date TEXT,
    release_age_days INTEGER,
    current_players INTEGER DEFAULT 0,
    followers INTEGER DEFAULT 0,
    delta_ccu INTEGER DEFAULT 0,
    delta_followers INTEGER DEFAULT 0,
    recent_news_count INTEGER DEFAULT 0,
    has_schinese INTEGER DEFAULT 0,
    has_trailer INTEGER DEFAULT 0,
    has_ai_disclosure INTEGER DEFAULT 0,
    developers_json TEXT,
    publishers_json TEXT,
    potential_score REAL DEFAULT 0.0,
    is_demo_radar INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS game_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid INTEGER NOT NULL,
    recommendation_id TEXT,
    voted_up INTEGER NOT NULL,
    votes_helpful INTEGER DEFAULT 0,
    playtime_hours REAL DEFAULT 0.0,
    quote_text TEXT NOT NULL,
    dimensions_json TEXT,
    review_url TEXT NOT NULL,
    FOREIGN KEY (appid) REFERENCES games(appid)
);
CREATE TABLE IF NOT EXISTS demo_metrics_history (
    appid INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,
    current_players INTEGER DEFAULT 0,
    followers INTEGER DEFAULT 0,
    PRIMARY KEY (appid, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_games_filter ON games(online_coop,horror_level,is_free,has_demo,review_score DESC);
CREATE INDEX IF NOT EXISTS idx_quotes_appid ON game_quotes(appid,voted_up);
CREATE INDEX IF NOT EXISTS idx_demo_metrics_date ON demo_metrics_history(appid,snapshot_date DESC);"""

JSON_FIELDS = {
    "categories_json",
    "tags_json",
    "avoid_reasons_json",
    "dimensions_json",
    "curators_json",
    "developers_json",
    "publishers_json",
}


def connect(path: str | Path = "data/games.sqlite") -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db(conn_or_path):
    c = connect(conn_or_path) if isinstance(conn_or_path, (str, Path)) else conn_or_path
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    existing_cols = {r[1] for r in c.execute("PRAGMA table_info(games)")}
    migrations = [
        ("short_desc_zh", "TEXT"),
        ("curator_count", "INTEGER DEFAULT 0"),
        ("curators_json", "TEXT"),
        ("coming_soon", "INTEGER DEFAULT 0"),
        ("release_date", "TEXT"),
        ("release_age_days", "INTEGER"),
        ("current_players", "INTEGER DEFAULT 0"),
        ("followers", "INTEGER DEFAULT 0"),
        ("delta_ccu", "INTEGER DEFAULT 0"),
        ("delta_followers", "INTEGER DEFAULT 0"),
        ("recent_news_count", "INTEGER DEFAULT 0"),
        ("has_schinese", "INTEGER DEFAULT 0"),
        ("has_trailer", "INTEGER DEFAULT 0"),
        ("has_ai_disclosure", "INTEGER DEFAULT 0"),
        ("developers_json", "TEXT"),
        ("publishers_json", "TEXT"),
        ("potential_score", "REAL DEFAULT 0.0"),
        ("is_demo_radar", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            c.execute(f"ALTER TABLE games ADD COLUMN {col_name} {col_def}")
    c.commit()
    return c


def record_demo_metric(conn, appid: int, snapshot_date: str, *, current_players: int = 0, followers: int = 0) -> None:
    conn.execute(
        "INSERT INTO demo_metrics_history (appid, snapshot_date, current_players, followers) VALUES (?,?,?,?) "
        "ON CONFLICT(appid, snapshot_date) DO UPDATE SET current_players=excluded.current_players, followers=excluded.followers",
        (int(appid), str(snapshot_date), int(current_players or 0), int(followers or 0)),
    )
    conn.commit()


def get_previous_demo_metric(conn, appid: int, before_date: str) -> dict | None:
    row = conn.execute(
        "SELECT snapshot_date, current_players, followers FROM demo_metrics_history "
        "WHERE appid=? AND snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1",
        (int(appid), str(before_date)),
    ).fetchone()
    return dict(row) if row else None


def prune_demo_metrics(conn, keep_dates: int = 14) -> None:
    rows = conn.execute(
        "SELECT DISTINCT snapshot_date FROM demo_metrics_history ORDER BY snapshot_date DESC LIMIT ?",
        (max(1, int(keep_dates)),),
    ).fetchall()
    if len(rows) >= keep_dates:
        oldest_keep = rows[-1][0]
        conn.execute("DELETE FROM demo_metrics_history WHERE snapshot_date < ?", (oldest_keep,))
        conn.commit()


def _encode(d):
    return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and not isinstance(v, str) else v) for k, v in d.items()}


def _decode(row):
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS:
        if k in d and d[k]:
            try:
                d[k] = json.loads(d[k])
            except (TypeError, json.JSONDecodeError):
                pass
    return d


def upsert_game(conn, game):
    fields = [r[1] for r in conn.execute("PRAGMA table_info(games)")]
    d = {k: v for k, v in _encode(dict(game)).items() if k in fields}
    d.setdefault("name", str(d.get("appid", "")))
    cols = list(d)
    conn.execute(
        f"INSERT INTO games ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) "
        f"ON CONFLICT(appid) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in cols if k != "appid"),
        [d[k] for k in cols],
    )
    conn.commit()


def insert_quote(conn, quote):
    d = _encode(dict(quote))
    cols = [
        k
        for k in d
        if k
        in {
            "appid",
            "recommendation_id",
            "voted_up",
            "votes_helpful",
            "playtime_hours",
            "quote_text",
            "dimensions_json",
            "review_url",
        }
    ]
    conn.execute(f"INSERT INTO game_quotes ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [d[k] for k in cols])
    conn.commit()


def get_game(conn, appid):
    return _decode(conn.execute("SELECT * FROM games WHERE appid=?", (appid,)).fetchone())


def search_games(conn, query, limit=20):
    return [
        _decode(r)
        for r in conn.execute("SELECT * FROM games WHERE name LIKE ? ORDER BY COALESCE(curator_count,0) DESC, review_score DESC LIMIT ?", ("%" + query + "%", limit))
    ]


def _matches_tag(row_dict: dict, tag: str | None) -> bool:
    if not tag:
        return True
    key = tag.strip().lower()
    targets = {t.lower() for t in TAG_ALIASES.get(key, [tag.strip()])}
    if key in {"demo", "试玩", "new_demo", "新品试玩"} and row_dict.get("has_demo"):
        return True
    haystack = []
    for field in ("tags_json", "categories_json"):
        vals = row_dict.get(field) or []
        if isinstance(vals, list):
            haystack.extend(str(v).lower() for v in vals)
        elif vals:
            haystack.append(str(vals).lower())
    haystack.append(str(row_dict.get("name", "")).lower())
    haystack.append(str(row_dict.get("short_desc", "")).lower())
    return any(t in item or item in t for t in targets for item in haystack if item)


PLAYABLE_SQL = (
    "(COALESCE(coming_soon, 0)=0 OR is_free=1 OR has_demo=1)"
    " AND NOT ("
    "COALESCE(is_free, 0)=0"
    " AND COALESCE(has_demo, 0)=0"
    " AND (price_cny IS NULL OR price_cny<=0)"
    " AND COALESCE(review_score, 0)=0"
    " AND COALESCE(total_reviews, 0)=0"
    ")"
)


def is_playable_game(game: dict) -> bool:
    """Return True only if the game can actually be downloaded/played tonight (released, free, or has Demo)."""
    is_free = bool(game.get("is_free"))
    has_demo = bool(game.get("has_demo")) or str(game.get("type", "")).lower() == "demo"
    if is_free or has_demo:
        return True
    if game.get("coming_soon"):
        return False
    price = game.get("price_cny")
    has_price = price is not None and float(price) > 0
    has_reviews = int(game.get("review_score") or 0) > 0 or int(game.get("total_reviews") or 0) > 0
    return has_price or has_reviews


def recommend_games(conn, *, horror="any", budget="any", players=None, mode="online", tag=None, limit=50):
    sql = f"SELECT * FROM games WHERE is_avoid=0 AND {PLAYABLE_SQL}"
    args = []
    if horror == "mild":
        sql += " AND (horror_level='mild' OR (horror_level='scary' AND (tags_json LIKE '%欢乐%' OR tags_json LIKE '%喜剧%')))"
    elif horror in {"none", "scary"}:
        sql += " AND horror_level=?"
        args.append(horror)
    if budget == "free_or_demo":
        sql += " AND (is_free=1 OR has_demo=1)"
    elif budget == "under_50":
        # paid under 50, free games, or downloadable free demos (never unreleased no-demo games)
        sql += " AND (is_free=1 OR (price_cny IS NOT NULL AND price_cny>0 AND price_cny<50) OR (has_demo=1 AND (price_cny IS NULL OR price_cny<=0)))"
    elif budget == "any_paid":
        sql += " AND is_free=0"
    if players:
        sql += " AND (max_players IS NULL OR max_players>=?)"
        args.append(players)
    if mode == "online":
        sql += " AND online_coop=1"
    elif mode == "local":
        sql += " AND local_coop=1"
    sql += (
        " ORDER BY CASE WHEN curators_json LIKE '%Steam%' OR COALESCE(curator_count, 0) > 0 THEN 2"
        " WHEN tags_json LIKE '%欢乐%' OR tags_json LIKE '%喜剧%' OR tags_json LIKE '%物理%' OR tags_json LIKE '%派对%' THEN 1 ELSE 0 END DESC,"
        " review_score DESC, CAST(COALESCE(positive_pct, 0) / 5.0 AS INTEGER) DESC,"
        " COALESCE(curator_count, 0) DESC, COALESCE(positive_pct, 0) DESC, COALESCE(potential_score, 0) DESC LIMIT ?"
    )
    args.append(max(limit * 4, 200) if tag else limit)
    rows = [_decode(r) for r in conn.execute(sql, args)]
    if tag:
        rows = [r for r in rows if _matches_tag(r, tag)]
    return rows[:limit]


def recommend_potential_demos(
    conn,
    *,
    max_age_days: int | None = 14,
    horror: str = "any",
    players: int | None = None,
    mode: str = "online",
    tag: str | None = None,
    min_score: float = 8.0,
    limit: int = 10,
):
    """Return high-potential recent/indie playable Demos ranked by early traction & polish score."""
    sql = (
        f"SELECT * FROM games WHERE is_avoid=0 AND (has_demo=1 OR type='demo') AND {PLAYABLE_SQL}"
        " AND COALESCE(potential_score, 0) >= ?"
    )
    args: list = [float(min_score)]
    if max_age_days is not None:
        sql += " AND (release_age_days IS NULL OR release_age_days <= ?)"
        args.append(int(max_age_days))
    if horror == "mild":
        sql += " AND (horror_level='mild' OR (horror_level='scary' AND (tags_json LIKE '%欢乐%' OR tags_json LIKE '%喜剧%')))"
    elif horror in {"none", "scary"}:
        sql += " AND horror_level=?"
        args.append(horror)
    if players:
        sql += " AND (max_players IS NULL OR max_players>=?)"
        args.append(int(players))
    if mode == "online":
        sql += " AND online_coop=1"
    elif mode == "local":
        sql += " AND local_coop=1"
    sql += (
        " ORDER BY COALESCE(potential_score, 0) DESC,"
        " COALESCE(current_players, 0) DESC,"
        " COALESCE(followers, 0) DESC,"
        " review_score DESC LIMIT ?"
    )
    args.append(max(limit * 4, 100) if tag else limit)
    rows = [_decode(r) for r in conn.execute(sql, args)]
    if tag:
        rows = [r for r in rows if _matches_tag(r, tag)]
    return rows[:limit]


def get_avoid_list(conn, limit=20):
    return [_decode(r) for r in conn.execute(f"SELECT * FROM games WHERE is_avoid=1 AND {PLAYABLE_SQL} ORDER BY review_score ASC LIMIT ?", (limit,))]
