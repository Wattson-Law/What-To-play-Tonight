"""FastMCP server for the co-op game picker (cloud snapshot + live curator fallback)."""
from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Literal, Optional

from . import db, extractor
from .constants import CURATOR_SOURCES, HORROR_TAG_IDS, REVIEW_TIERS
from .steam_client import SteamClient

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised in minimal installs
    FastMCP = None
    _MCP_IMPORT_ERROR = exc
else:
    _MCP_IMPORT_ERROR = None

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "games.sqlite"
DB_PATH = Path(os.getenv("COOP_NIGHT_PICKER_DB", str(DEFAULT_DB_PATH)))
USER_LOG_PATH = Path(os.getenv("COOP_NIGHT_PICKER_USER_LOG", str(ROOT / "data" / "user_log.json")))
CLOUD_DB_URL = os.getenv(
    "COOP_NIGHT_PICKER_CLOUD_DB_URL",
    "https://raw.githubusercontent.com/Wattson-Law/What-To-play-Tonight/main/data/games.sqlite",
)
CLOUD_CACHE_PATH = Path(tempfile.gettempdir()) / "coop-night-picker-cloud-games.sqlite"

if FastMCP is not None:
    mcp = FastMCP("今晚玩什么 (coop-night-picker)")
else:
    mcp = None


def _load_log(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else USER_LOG_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"records": []}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"records": []}


def _records(log: dict) -> list[dict]:
    records = log.get("records", [])
    if isinstance(records, list):
        return [r for r in records if isinstance(r, dict)]
    return []


def _excluded(log: dict) -> set[int]:
    return {
        int(r["appid"])
        for r in _records(log)
        if r.get("status") in {"played", "avoided"} and str(r.get("appid", "")).isdigit()
    }


def _save_log(log: dict, path: Path | None = None) -> None:
    path = Path(path) if path is not None else USER_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".user_log.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(log, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _download_cloud_snapshot(target_path: Path | None = None) -> bool:
    if not CLOUD_DB_URL or os.getenv("COOP_NIGHT_PICKER_DISABLE_CLOUD") == "1":
        return False
    dest = Path(target_path) if target_path is not None else CLOUD_CACHE_PATH
    try:
        req = urllib.request.Request(CLOUD_DB_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            if resp.status == 200 and data.startswith(b"SQLite format 3"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return True
    except Exception:
        return False
    return False


def _open_snapshot():
    if DB_PATH.exists():
        try:
            return db.init_db(DB_PATH)
        except Exception:
            return None
    if DB_PATH == DEFAULT_DB_PATH and _download_cloud_snapshot(CLOUD_CACHE_PATH) and CLOUD_CACHE_PATH.exists():
        try:
            return db.init_db(CLOUD_CACHE_PATH)
        except Exception:
            return None
    return None


def _question_list(horror, budget, players, session_duration=None):
    questions = []
    if horror is None:
        questions.append(
            {
                "param": "horror",
                "question": "恐怖游戏能接受吗？",
                "options": ["一点都不行 (none)", "微恐/心理恐怖可以 (mild)", "越吓人越好 (scary)", "都行 (any)"],
            }
        )
    if budget is None:
        questions.append(
            {
                "param": "budget",
                "question": "想白嫖免费游戏/试玩 Demo，还是愿意花钱买？",
                "options": ["只看免费或有免费Demo的 (free_or_demo)", "50元以内便宜好玩的 (under_50)", "价格无所谓，好玩就行 (any)"],
            }
        )
    if players is None:
        questions.append({"param": "players", "question": "今晚大概几个人一起玩？（比如 2 人、4 人）", "options": ["2人", "3-4人", "5人以上"]})
    if session_duration is None:
        questions.append({"param": "session_duration", "question": "今晚准备玩多久？", "options": ["短局（30-60分钟）", "一两个小时", "不限时"]})
    return questions


def _filtered(conn, horror, budget, players, mode, excluded, tag=None):
    rows = db.recommend_games(conn, horror=horror, budget=budget, players=players, mode=mode, tag=tag, limit=10000)
    return [r for r in rows if int(r.get("appid", 0)) not in excluded]


def _format_game(game, conn=None):
    score = int(game.get("review_score") or 0)
    tier = REVIEW_TIERS.get(score, REVIEW_TIERS[0])
    curators = game.get("curators_json") or []
    if isinstance(curators, str):
        try:
            curators = json.loads(curators)
        except Exception:
            curators = [curators]
    tags = game.get("tags_json") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [tags]
    formatted = {
        "appid": int(game["appid"]),
        "name": game.get("name", ""),
        "type": game.get("type", "game"),
        "review_score": score,
        "review_tier": tier["zh"],
        "stars": tier["stars"],
        "positive_pct": game.get("positive_pct", 0),
        "schinese_pct": game.get("schinese_pct"),
        "total_reviews": game.get("total_reviews", 0),
        "price_cny": game.get("price_cny", 0),
        "is_free": bool(game.get("is_free")),
        "has_demo": bool(game.get("has_demo")),
        "horror_level": game.get("horror_level", "none"),
        "max_players": game.get("max_players"),
        "online_coop": bool(game.get("online_coop", True)),
        "local_coop": bool(game.get("local_coop")),
        "short_desc": game.get("short_desc") or "",
        "tags": tags,
        "curator_count": int(game.get("curator_count") or len(curators)),
        "curators": curators,
        "avoid_reasons": game.get("avoid_reasons_json") or game.get("avoid_reasons") or [],
    }
    if conn is not None:
        try:
            qs = conn.execute(
                "SELECT * FROM game_quotes WHERE appid=? AND voted_up=1 ORDER BY votes_helpful DESC LIMIT 2",
                (game["appid"],),
            ).fetchall()
            formatted["quotes"] = [db._decode(q) for q in qs]
            qs = conn.execute(
                "SELECT * FROM game_quotes WHERE appid=? AND voted_up=0 ORDER BY votes_helpful DESC LIMIT 1",
                (game["appid"],),
            ).fetchall()
            formatted["negative_quotes"] = [db._decode(q) for q in qs]
        except Exception:
            formatted["quotes"] = []
    else:
        formatted["quotes"] = game.get("quotes", [])
        formatted["negative_quotes"] = game.get("negative_quotes", [])
    return formatted


def _markdown(groups, avoid, players, horror, budget, mode="online", tag=None):
    mode_label = {"online": "online", "local": "local/co-op", "any": "online/local"}.get(mode, mode)
    tag_suffix = f"｜{tag}" if tag else ""
    title = f"**🎮 今晚玩什么 · 筛选结果**（{players}人｜{horror}｜{budget}｜{mode_label}{tag_suffix}）"
    lines = [title, ""]
    for score, games in groups.items():
        tier = REVIEW_TIERS[int(score)]
        if int(score) == 0:
            lines += ["**【🧪 整活新品 & 热门愿望单试玩专区（未发先火 / 刚出新游 / 免费Demo）】**"]
        else:
            lines += [f"**【{tier['stars']} {tier['zh']}（Score {score}）】**"]
        display_cap = 2 if len(groups) >= 3 else 3
        for i, g in enumerate(games[:display_cap], 1):
            price = "免费" if g["is_free"] else (f"¥{float(g['price_cny']):g}" if g.get("price_cny") else "即将推出/免费试玩")
            if g.get("has_demo") and not g["is_free"]:
                price += "（含免费Demo）"
            coop_label = "在线合作" if mode == "online" else ("本地合作" if mode == "local" else "在线/本地合作")
            lines.append(f"{i}. **《{g['name']}》** — {price} | 支持 {g.get('max_players') or '?'} 人{coop_label}")
            if g.get("short_desc"):
                lines.append(f"   - 🎯 **玩法设定**：{g['short_desc']}")
            if g.get("curator_count"):
                curator_names = "、".join(g.get("curators") or [])
                lines.append(f"   - 🏅 **鉴赏家/趋势精选**：入选 {g['curator_count']} 个精选榜单（{curator_names}）")
            pos_pct = f"{float(g.get('positive_pct') or 0):.1f}%"
            sc_pct = f"{float(g['schinese_pct']):.1f}%" if g.get("schinese_pct") is not None else "—"
            lines.append(
                f"   - 📊 **口碑**：全语言 {pos_pct}（{tier['zh']}，{g.get('total_reviews', 0)}篇）"
                f"｜简中 {sc_pct}"
            )
            quotes = g.get("quotes", [])
            for q in quotes[:1]:
                q_text = q.get("quote_text", "")
                if not q_text:
                    continue
                if str(q_text).startswith("[鉴赏家·"):
                    lines.append(f"   - 💡 **推荐理由（来自Steam鉴赏家评测）**：> {q_text}（[查看评测]({q.get('review_url', '')})）")
                else:
                    lines.append(
                        f"   - 💡 **推荐理由（来自玩家评测）**：> {q_text}"
                        f"（👍 {q.get('votes_helpful', 0)} 人觉得有用 · [查看原评]({q.get('review_url', '')})）"
                    )
        lines.append("")
    lines += ["---", "**【⚠️ 避雷榜（同筛选条件下容易踩坑的游戏）】**"]
    avoid_lines = []
    for g in avoid:
        reasons = g.get("avoid_reasons") or []
        reason_text = "、".join(map(str, reasons)) if isinstance(reasons, list) else str(reasons)
        avoid_lines.append(
            f"- ❌ **《{g['name']}》** — {reason_text or REVIEW_TIERS.get(int(g.get('review_score') or 0), REVIEW_TIERS[0])['zh']}"
        )
        for q in (g.get("negative_quotes") or [])[:1]:
            avoid_lines.append(
                f"  - 💡 **避雷证据（来自玩家评测）**：> {q.get('quote_text', '')}"
                f"（👍 {q.get('votes_helpful', 0)} 人觉得有用 · [原评]({q.get('review_url', '')})）"
            )
    lines += avoid_lines or ["- 暂无"]
    return "\n".join(lines)


def _live_curator_recommend(horror, budget, players, mode, excluded, limit_per_tier, session_duration, tag):
    client = SteamClient()
    by_app: dict[int, dict] = {}
    for curator in CURATOR_SOURCES[:4]:
        raw = client.curator_recommendations(curator["clanid"], tagids="3843", start=0, count=25)
        if isinstance(raw, dict) and raw.get("unavailable"):
            continue
        for item in extractor.parse_curator_recommendations(raw, curator_name=curator["name"], clanid=curator["clanid"]):
            aid = int(item["appid"])
            if aid in excluded:
                continue
            tag_ids = set(item.get("tag_ids", []))
            h_level = "scary" if {1667, 3978} & tag_ids else ("mild" if 1721 in tag_ids else "none")
            rec = by_app.setdefault(
                aid,
                {
                    "appid": aid,
                    "name": item["name"],
                    "type": "demo" if item.get("has_demo") else "game",
                    "review_score": 8 if item.get("voted_up") else 3,
                    "positive_pct": 90.0 if item.get("voted_up") else 35.0,
                    "schinese_pct": None,
                    "total_reviews": 0,
                    "price_cny": item.get("price_cny", 0.0),
                    "is_free": item.get("is_free", 0),
                    "has_demo": item.get("has_demo", 0),
                    "horror_level": h_level,
                    "max_players": item.get("max_players"),
                    "online_coop": 1,
                    "local_coop": int(3841 in tag_ids or 7368 in tag_ids),
                    "tags_json": item.get("tags_json", []),
                    "curators_json": [],
                    "curator_count": 0,
                    "quotes": [],
                    "negative_quotes": [],
                    "avoid_reasons_json": [],
                    "is_avoid": 0,
                },
            )
            quote_obj = {
                "quote_text": item["quote_text"],
                "votes_helpful": 999 if item.get("voted_up") else 500,
                "review_url": item["review_url"],
                "voted_up": item.get("voted_up", 1),
            }
            if item.get("voted_up"):
                if curator["name"] not in rec["curators_json"]:
                    rec["curators_json"].append(curator["name"])
                    rec["curator_count"] += 1
                rec["quotes"].append(quote_obj)
            else:
                rec["is_avoid"] = 1
                rec["avoid_reasons_json"].append(f"鉴赏家避雷({curator['name']})")
                rec["negative_quotes"].append(quote_obj)

    if not by_app:
        return None

    groups = {}
    avoid = []
    for g in by_app.values():
        if horror in {"none", "mild", "scary"} and g["horror_level"] != horror:
            continue
        if players and g.get("max_players") and int(g["max_players"]) < players:
            continue
        if budget == "free_or_demo" and not (g["is_free"] or g["has_demo"]):
            continue
        if budget == "under_50" and float(g.get("price_cny") or 0) >= 50:
            continue
        if budget == "any_paid" and g["is_free"]:
            continue
        if tag and not db._matches_tag(g, tag):
            continue
        fmt = _format_game(g, None)
        if g.get("is_avoid"):
            avoid.append(fmt)
        else:
            groups.setdefault(str(fmt["review_score"]), []).append(fmt)

    for k in list(groups.keys()):
        groups[k] = sorted(groups[k], key=lambda x: x.get("curator_count", 0), reverse=True)[: max(1, int(limit_per_tier))]
    query = {"horror": horror, "budget": budget, "players": players, "mode": mode, "session_duration": session_duration, "tag": tag}
    return {
        "status": "ok",
        "groups": groups,
        "avoid_list": avoid[:3],
        "query": query,
        "meta": {
            "source": "live_curators",
            "excluded_appids": sorted(excluded),
            "count": sum(map(len, groups.values())),
            "session_duration": session_duration,
            "session_duration_advisory": True,
        },
        "markdown_preview": _markdown(groups, avoid[:3], players, horror, budget, mode, tag=tag),
    }


def recommend_coop_games(
    horror: Optional[Literal["none", "mild", "scary", "any"]] = None,
    budget: Optional[Literal["free_or_demo", "under_50", "any_paid", "any"]] = None,
    players: Optional[int] = None,
    mode: Literal["online", "local", "any"] = "online",
    confirmed: bool = False,
    limit_per_tier: int = 3,
    session_duration: Optional[str] = None,
    tag: Optional[str] = None,
) -> dict:
    questions = _question_list(horror, budget, players, session_duration)
    if not confirmed and questions:
        return {
            "status": "need_more_info",
            "instruction_for_assistant": "请在一轮回复内问完这些问题；收到答案后传入具体参数并设置 confirmed=True。",
            "questions": questions,
        }
    if horror is None or budget is None or players is None:
        return {
            "status": "need_more_info",
            "instruction_for_assistant": "请补齐筛选参数并设置 confirmed=True。",
            "questions": _question_list(horror, budget, players, session_duration),
        }
    log = _load_log()
    excluded = _excluded(log)
    conn = _open_snapshot()
    if conn is None:
        if DB_PATH == DEFAULT_DB_PATH:
            live = _live_curator_recommend(horror, budget, players, mode, excluded, limit_per_tier, session_duration, tag)
            if live is not None:
                return live
        return {
            "status": "empty_snapshot",
            "message": "本地游戏快照不存在或无法读取，请等待 GitHub Actions 生成 data/games.sqlite。",
            "groups": {},
            "avoid_list": [],
            "markdown_preview": "",
        }
    try:
        if conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0:
            return {
                "status": "empty_snapshot",
                "message": "本地游戏快照为空，请先生成数据。",
                "groups": {},
                "avoid_list": [],
                "markdown_preview": "",
            }
        games = _filtered(conn, horror, budget, players, mode, excluded, tag=tag)
        groups = {}
        for game in games:
            score = int(game.get("review_score") or 0)
            groups.setdefault(score, []).append(_format_game(game, conn))
        groups = {str(k): v[: max(1, int(limit_per_tier))] for k, v in sorted(groups.items(), reverse=True)}
        ids = {g["appid"] for values in groups.values() for g in values}
        avoid = [
            _format_game(g, conn)
            for g in db.get_avoid_list(conn, 20)
            if int(g["appid"]) not in excluded
            and int(g["appid"]) not in ids
            and (horror in {"any", g.get("horror_level", "none")})
            and (not players or g.get("max_players") is None or int(g.get("max_players")) >= players)
            and (mode == "any" or (mode == "online" and g.get("online_coop")) or (mode == "local" and g.get("local_coop")))
            and (not tag or db._matches_tag(g, tag))
        ]
        avoid = [
            g
            for g in avoid
            if budget == "any"
            or (budget == "free_or_demo" and (g["is_free"] or g["has_demo"]))
            or (budget == "under_50" and float(g.get("price_cny") or 0) < 50)
            or (budget == "any_paid" and not g["is_free"])
        ][:3]
        query = {"horror": horror, "budget": budget, "players": players, "mode": mode, "session_duration": session_duration, "tag": tag}
        return {
            "status": "ok",
            "groups": groups,
            "avoid_list": avoid,
            "query": query,
            "meta": {
                "source": "snapshot",
                "excluded_appids": sorted(excluded),
                "count": sum(map(len, groups.values())),
                "session_duration": session_duration,
                "session_duration_advisory": True,
            },
            "markdown_preview": _markdown(groups, avoid, players, horror, budget, mode, tag=tag),
        }
    finally:
        conn.close()


def get_game_card(appid: int, include_quotes: int = 3) -> dict:
    conn = _open_snapshot()
    if conn is None:
        return {"status": "empty_snapshot", "appid": appid, "message": "本地游戏快照不存在。"}
    try:
        game = db.get_game(conn, appid)
        if game is None:
            return {"status": "not_found", "appid": appid}
        card = _format_game(game, conn)
        rows = conn.execute(
            "SELECT * FROM game_quotes WHERE appid=? AND voted_up=1 ORDER BY votes_helpful DESC LIMIT ?",
            (appid, max(1, include_quotes)),
        ).fetchall()
        rows += conn.execute(
            "SELECT * FROM game_quotes WHERE appid=? AND voted_up=0 ORDER BY votes_helpful DESC LIMIT ?",
            (appid, max(1, include_quotes)),
        ).fetchall()
        card["quotes"] = [db._decode(r) for r in rows]
        card["positive_quotes"] = [q for q in card["quotes"] if q.get("voted_up")][:include_quotes]
        card["negative_quotes"] = [q for q in card["quotes"] if not q.get("voted_up")][:include_quotes]
        card["status"] = "ok"
        return card
    finally:
        conn.close()


def resolve_game(query: str, limit: int = 5) -> dict:
    conn = _open_snapshot()
    local = []
    if conn is not None:
        try:
            local = db.search_games(conn, query, limit)
        finally:
            conn.close()
    result = {"status": "ok", "query": query, "items": local[:limit], "meta": {"source": "local", "network": None}}
    if len(local) >= limit:
        return result
    try:
        remote = SteamClient().store_search(query)
        if isinstance(remote, dict) and remote.get("unavailable"):
            result["meta"]["network"] = {
                "ok": False,
                "error": remote.get("error", "Steam search unavailable"),
                "status_code": remote.get("status_code"),
            }
            return result
        items = remote.get("items", []) if isinstance(remote, dict) else (remote if isinstance(remote, list) else [])
        result["items"].extend(items[: max(0, limit - len(local))])
        result["meta"]["source"] = "local+steam"
    except Exception as exc:
        result["meta"]["network"] = {"ok": False, "error": str(exc)}
    return result


def log_play(appid: int, status: Literal["played", "avoided", "wishlist"], note: str = "") -> dict:
    if status not in {"played", "avoided", "wishlist"}:
        return {"status": "error", "message": "status 必须是 played、avoided 或 wishlist。"}
    log = _load_log()
    records = _records(log)
    records.append({"appid": int(appid), "status": status, "note": note})
    log["records"] = records
    _save_log(log)
    return {
        "status": "saved",
        "appid": int(appid),
        "saved_status": status,
        "excluded": status in {"played", "avoided"},
        "excluded_appid": int(appid) if status in {"played", "avoided"} else None,
    }


def tonight_playbook() -> str:
    return (
        "请最多用一轮回复询问四个问题：人数、恐怖接受度、预算（免费/Demo、50元以内或不限）和游玩时长（如有偏好类型如独立/生存也可一并传入 tag）；"
        "随后调用 recommend_coop_games 并设置 confirmed=True，优先展示有 Steam 鉴赏家精选背书的游戏、星级、鉴赏家/玩家双轨理由和避雷榜。"
    )


if mcp is not None:
    recommend_coop_games = mcp.tool()(recommend_coop_games)
    get_game_card = mcp.tool()(get_game_card)
    resolve_game = mcp.tool()(resolve_game)
    log_play = mcp.tool()(log_play)
    tonight_playbook = mcp.prompt()(tonight_playbook)


def main() -> None:
    if mcp is None:
        raise RuntimeError("FastMCP is unavailable. Install the optional MCP dependency with `pip install 'mcp[cli]'`.") from _MCP_IMPORT_ERROR
    mcp.run()


if __name__ == "__main__":
    main()
