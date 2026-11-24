"""FastMCP server for the co-op game picker (cloud snapshot + live curator fallback)."""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal, Optional

from . import db, extractor, state_store
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
_CLOUD_ETAG: str | None = None
_LAST_CLOUD_CHECK_AT: float = 0.0

if FastMCP is not None:
    mcp = FastMCP("今晚玩什么 (coop-night-picker)")
else:
    mcp = None


def _load_log(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else USER_LOG_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return state_store.normalize_state(value)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return state_store.normalize_state({})


def _records(log: dict, scope: str = "default") -> list[dict]:
    norm = state_store.normalize_state(log)
    bucket = norm.get("scopes", {}).get(scope or "default") or {}
    records = bucket.get("records", [])
    if isinstance(records, list):
        return [r for r in records if isinstance(r, dict)]
    return []


def _excluded(log: dict, scope: str = "default") -> set[int]:
    return {
        int(r["appid"])
        for r in _records(log, scope=scope)
        if r.get("status") in {"played", "avoided"} and str(r.get("appid", "")).isdigit()
    }


def _save_log(log: dict, path: Path | None = None, *, sync_cloud: bool = True) -> None:
    path = Path(path) if path is not None else USER_LOG_PATH
    norm = state_store.normalize_state(log)
    # Mirror default scope into top-level "records" for legacy readers/tests
    default_recs = (norm.get("scopes", {}).get("default") or {}).get("records", [])
    disk_payload = {
        "version": 2,
        "records": default_recs,
        "scopes": norm["scopes"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".user_log.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(disk_payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    if sync_cloud and path == USER_LOG_PATH:
        store = state_store.get_default_cloud_store()
        if store is not None:
            try:
                store.push(norm)
            except Exception:
                pass


def sync_cloud_user_log() -> bool:
    """Pull cloud-persisted play/dislike logs from GitHub bot-state branch and merge with local."""
    store = state_store.get_default_cloud_store()
    if store is None:
        return False
    remote = store.pull()
    if remote is None:
        return False
    local = _load_log()
    merged = state_store.merge_state_payloads(remote, local)
    _save_log(merged, sync_cloud=False)
    return True


def _download_cloud_snapshot(target_path: Path | None = None, *, use_etag: bool = False) -> bool:
    global _CLOUD_ETAG
    if not CLOUD_DB_URL or os.getenv("COOP_NIGHT_PICKER_DISABLE_CLOUD") == "1":
        return False
    dest = Path(target_path) if target_path is not None else CLOUD_CACHE_PATH
    headers = {"User-Agent": "Mozilla/5.0"}
    if use_etag and _CLOUD_ETAG and dest.exists():
        headers["If-None-Match"] = _CLOUD_ETAG
    try:
        req = urllib.request.Request(CLOUD_DB_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            if resp.status == 200 and data.startswith(b"SQLite format 3"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(prefix=".cloud-games.", suffix=".tmp", dir=dest.parent)
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(data)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp_name, dest)
                finally:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
                etag = resp.headers.get("ETag") if hasattr(resp, "headers") and resp.headers else None
                if etag:
                    _CLOUD_ETAG = etag
                return True
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return False
        return False
    except Exception:
        return False
    return False


def refresh_cloud_snapshot(*, min_interval_sec: float = 1800.0, force: bool = False) -> bool:
    """Check GitHub raw snapshot via ETag and hot-swap CLOUD_CACHE_PATH when a newer build lands."""
    global _LAST_CLOUD_CHECK_AT
    if DB_PATH != DEFAULT_DB_PATH or os.getenv("COOP_NIGHT_PICKER_DISABLE_CLOUD") == "1":
        return False
    now = time.time()
    if not force and now - _LAST_CLOUD_CHECK_AT < min_interval_sec:
        return False
    _LAST_CLOUD_CHECK_AT = now
    return _download_cloud_snapshot(CLOUD_CACHE_PATH, use_etag=True)


def _open_snapshot():
    if DB_PATH.exists():
        # Prefer a fresher hot-reloaded cloud cache when running against the default checkout DB
        if (
            DB_PATH == DEFAULT_DB_PATH
            and os.getenv("COOP_NIGHT_PICKER_DISABLE_CLOUD") != "1"
            and CLOUD_CACHE_PATH.exists()
            and CLOUD_CACHE_PATH.stat().st_mtime > DB_PATH.stat().st_mtime
        ):
            try:
                return db.init_db(CLOUD_CACHE_PATH)
            except Exception:
                pass
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
        "short_desc_zh": game.get("short_desc_zh") or "",
        "tags": tags,
        "curator_count": int(game.get("curator_count") or len(curators)),
        "curators": curators,
        "avoid_reasons": game.get("avoid_reasons_json") or game.get("avoid_reasons") or [],
        "release_date": game.get("release_date") or "",
        "release_age_days": game.get("release_age_days"),
        "current_players": int(game.get("current_players") or 0),
        "followers": int(game.get("followers") or 0),
        "delta_ccu": int(game.get("delta_ccu") or 0),
        "delta_followers": int(game.get("delta_followers") or 0),
        "recent_news_count": int(game.get("recent_news_count") or 0),
        "has_schinese": bool(game.get("has_schinese")),
        "has_trailer": bool(game.get("has_trailer")),
        "has_ai_disclosure": bool(game.get("has_ai_disclosure")),
        "potential_score": float(game.get("potential_score") or 0.0),
        "is_demo_radar": bool(game.get("is_demo_radar")),
        "demo_appid": game.get("demo_appid"),
        "fullgame_appid": game.get("fullgame_appid"),
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


_HORROR_ZH = {"none": "不恐怖", "mild": "微恐", "scary": "恐怖", "any": "不限恐怖"}
_BUDGET_ZH = {"free_or_demo": "免费/Demo", "under_50": "50元以内", "any_paid": "付费买断", "any": "不限预算"}
_MODE_ZH = {"online": "在线合作", "local": "本地同屏", "any": "在线/本地"}


def _fmt_title(name: str) -> str:
    n = str(name or "").strip()
    return n if (n.startswith("《") and "》" in n) else f"《{n}》"


def _compact_url(url: str) -> str:
    # Strip verbose Steam snr tracking tokens so KMarkdown replies stay compact (fit in 1 KOOK message)
    u = str(url or "").strip()
    if "snr=" in u and "store.steampowered.com/app/" in u:
        import re as _re
        u = _re.sub(r"([?&])snr=[^&]+&?", r"\1", u).rstrip("?&")
    return u


def _truncate(text: str | None, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rstrip(" ，,。.;；:：-")
    # Avoid chopping the middle of an ASCII word
    if cut and cut[-1].isascii() and cut[-1].isalnum() and len(clean) > len(cut) and clean[len(cut)].isascii() and clean[len(cut)].isalnum():
        space = cut.rfind(" ")
        if space >= limit // 2:
            cut = cut[:space]
    return cut.rstrip(" ，,。.;；:：-") + "…"


def _dedupe_demo_pairs(rows: list[dict]) -> list[dict]:
    """Collapse Demo + Fullgame duplicates referring to the same title."""
    seen: set[int] = set()
    out: list[dict] = []
    for row in rows:
        aid = int(row.get("appid") or 0)
        linked = {aid}
        if row.get("demo_appid"):
            linked.add(int(row["demo_appid"]))
        if row.get("fullgame_appid"):
            linked.add(int(row["fullgame_appid"]))
        if linked & seen:
            continue
        seen.update(linked)
        out.append(row)
    return out


def _select_display_items(groups: dict, max_total: int = 6) -> list[tuple[int, dict]]:
    """Select flat (score, game) display pairs in exact markdown rendering order."""
    selected: list[tuple[int, dict]] = []
    tier_items = list(groups.items())
    for score, games in tier_items:
        if len(selected) >= max_total:
            break
        cap = min(2 if len(tier_items) >= 3 else 3, max_total - len(selected))
        for g in games[:cap]:
            selected.append((int(score), g))
    return selected


def _markdown(groups, avoid, players, horror, budget, mode="online", tag=None):
    badges = [
        f"`{players}人`",
        f"`{_HORROR_ZH.get(horror, horror)}`",
        f"`{_BUDGET_ZH.get(budget, budget)}`",
        f"`{_MODE_ZH.get(mode, mode)}`",
    ]
    if tag:
        badges.append(f"`{tag}`")
    lines = [f"**🎮 今晚玩什么 · 筛选结果**  {' '.join(badges)}"]

    shown_total = 0
    max_total = 6
    tier_items = list(groups.items())
    for tier_idx, (score, games) in enumerate(tier_items):
        if shown_total >= max_total:
            break
        tier = REVIEW_TIERS[int(score)]
        cap = min(2 if len(tier_items) >= 3 else 3, max_total - shown_total)
        batch = games[:cap]
        if not batch:
            continue
        lines.append("")
        if tier_idx > 0:
            lines.append("---")
        if int(score) == 0:
            lines.append("**🧪 整活新品 & 免费试玩专区**")
        else:
            lines.append(f"**{tier['stars']} {tier['zh']}**")

        for g in batch:
            shown_total += 1
            if g["is_free"]:
                price = "`免费`"
            elif g.get("price_cny"):
                price = f"`¥{float(g['price_cny']):g}`"
                if g.get("has_demo"):
                    price += " `含Demo`"
            elif g.get("has_demo"):
                price = "`免费Demo`"
            else:
                price = "`待定`"

            meta_parts = [price]
            if g.get("max_players"):
                meta_parts.append(f"`最多{g['max_players']}人`")
            if int(score) > 0:
                pos_pct = f"{float(g.get('positive_pct') or 0):.1f}%"
                if g.get("schinese_pct") is not None:
                    meta_parts.append(f"好评 {pos_pct}（简中 {float(g['schinese_pct']):.1f}%）")
                elif g.get("total_reviews"):
                    meta_parts.append(f"好评 {pos_pct}（{g['total_reviews']}篇）")
                else:
                    meta_parts.append(f"好评 {pos_pct}")
            else:
                if g.get("current_players"):
                    meta_parts.append(f"当前在线 {g['current_players']} 人")
                if g.get("followers"):
                    meta_parts.append(f"关注 {g['followers']} 人")
                if g.get("release_age_days") is not None:
                    meta_parts.append(f"上线{g['release_age_days']}天")

            lines.append("")
            lines.append(f"**{shown_total}. {_fmt_title(g['name'])}**  {' · '.join(meta_parts)}")

            desc = _truncate(g.get("short_desc_zh") or g.get("short_desc"), 64)
            steam_url = f"https://store.steampowered.com/app/{g.get('demo_appid') or g['appid']}/"
            if desc:
                lines.append(f"{desc}  [商店页]({steam_url})")
            else:
                lines.append(f"🔗 [前往 Steam]({steam_url})")

            human_curators = [c for c in (g.get("curators") or []) if not str(c).startswith("Steam")]
            quotes = g.get("quotes") or []
            rendered_quote = False
            for q in quotes[:1]:
                q_text = str(q.get("quote_text") or "").strip()
                if not q_text:
                    continue
                rendered_quote = True
                if q_text.startswith("[鉴赏家·"):
                    end_bracket = q_text.find("]")
                    curator_label = q_text[5:end_bracket] if end_bracket > 5 else "鉴赏家"
                    body = _truncate(q_text[end_bracket + 1 :].strip(), 72) if end_bracket > 0 else _truncate(q_text, 72)
                    url = _compact_url(q.get("review_url") or steam_url)
                    lines.append(f"> 🏅 鉴赏家 **{curator_label}**：{body}（[查看评测]({url})）")
                else:
                    body = _truncate(q_text, 72)
                    url = _compact_url(q.get("review_url") or steam_url)
                    lines.append(f"> 💬 玩家评：{body}（👍{q.get('votes_helpful', 0)} · [原评]({url})）")
            if not rendered_quote and human_curators:
                lines.append(f"🏅 鉴赏家推荐：{'、'.join(human_curators[:3])}")

    lines.extend(["", "---", "**⚠️ 避雷榜**"])
    if avoid:
        for g in avoid[:3]:
            reasons = g.get("avoid_reasons") or []
            reason_text = "、".join(map(str, reasons)) if isinstance(reasons, list) else str(reasons)
            fallback_tier = REVIEW_TIERS.get(int(g.get("review_score") or 0), REVIEW_TIERS[0])["zh"]
            lines.append(f"• **{_fmt_title(g['name'])}** — {reason_text or fallback_tier}")
            for q in (g.get("negative_quotes") or [])[:1]:
                q_body = _truncate(q.get("quote_text"), 75)
                if q_body:
                    lines.append(f"> 避雷证据（来自玩家评测）：{q_body}（[原评]({q.get('review_url', '')})）")
    else:
        lines.append("• 暂无")

    lines.extend(["", "💡 直接回 `第2个不好玩，推荐点别的` / `玩过 游戏名` / `换一批` 自动记云端并换新"])
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
        if not db.is_playable_game(g):
            continue
        if horror in {"none", "mild", "scary"} and g["horror_level"] != horror:
            continue
        if players and g.get("max_players") and int(g["max_players"]) < players:
            continue
        if budget == "free_or_demo" and not (g["is_free"] or g["has_demo"]):
            continue
        if budget == "under_50":
            price_val = g.get("price_cny")
            is_under_50 = bool(g["is_free"]) or (price_val is not None and 0 < float(price_val) < 50) or (bool(g["has_demo"]) and not price_val)
            if not is_under_50:
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
    displayed = [g for _, g in _select_display_items(groups)]
    query = {"horror": horror, "budget": budget, "players": players, "mode": mode, "session_duration": session_duration, "tag": tag}
    return {
        "status": "ok",
        "groups": groups,
        "recommended": displayed,
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
    scope: str = "default",
    extra_excluded: Optional[list[int]] = None,
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
    excluded = _excluded(log, scope=scope)
    if extra_excluded:
        excluded |= {int(x) for x in extra_excluded if str(x).isdigit()}
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
        games = _dedupe_demo_pairs(_filtered(conn, horror, budget, players, mode, excluded, tag=tag))
        groups = {}
        for game in games:
            score = int(game.get("review_score") or 0)
            groups.setdefault(score, []).append(_format_game(game, conn))
        groups = {str(k): v[: max(1, int(limit_per_tier))] for k, v in sorted(groups.items(), reverse=True)}
        ids = {g["appid"] for values in groups.values() for g in values}
        def _avoid_matches_horror(g: dict) -> bool:
            if horror == "any":
                return True
            hl = g.get("horror_level", "none")
            if horror == "mild":
                tags = " ".join(map(str, g.get("tags_json") or []))
                return hl == "mild" or (hl == "scary" and ("欢乐" in tags or "喜剧" in tags))
            return hl == horror

        avoid = [
            _format_game(g, conn)
            for g in db.get_avoid_list(conn, 20)
            if int(g["appid"]) not in excluded
            and int(g["appid"]) not in ids
            and _avoid_matches_horror(g)
            and (not players or g.get("max_players") is None or int(g.get("max_players")) >= players)
            and (mode == "any" or (mode == "online" and g.get("online_coop")) or (mode == "local" and g.get("local_coop")))
            and (not tag or db._matches_tag(g, tag))
        ]
        avoid = [
            g
            for g in avoid
            if budget == "any"
            or (budget == "free_or_demo" and (g["is_free"] or g["has_demo"]))
            or (
                budget == "under_50"
                and (
                    g["is_free"]
                    or (g.get("price_cny") is not None and 0 < float(g["price_cny"]) < 50)
                    or (g["has_demo"] and not g.get("price_cny"))
                )
            )
            or (budget == "any_paid" and not g["is_free"])
        ][:3]
        displayed = [g for _, g in _select_display_items(groups)]
        query = {"horror": horror, "budget": budget, "players": players, "mode": mode, "session_duration": session_duration, "tag": tag}
        return {
            "status": "ok",
            "groups": groups,
            "recommended": displayed,
            "avoid_list": avoid,
            "query": query,
            "meta": {
                "source": "snapshot",
                "scope": scope,
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
        remote = SteamClient().store_search(query, l="schinese", cc="cn")
        if isinstance(remote, dict) and remote.get("unavailable"):
            result["meta"]["network"] = {
                "ok": False,
                "error": remote.get("error", "Steam search unavailable"),
                "status_code": remote.get("status_code"),
            }
            return result
        if isinstance(remote, str):
            items = extractor.parse_search_html(remote)
        elif isinstance(remote, dict):
            items = remote.get("items") or extractor.parse_search_html(
                remote.get("results_html") or remote.get("html") or remote.get("text") or ""
            )
        elif isinstance(remote, list):
            items = remote
        else:
            items = []
        seen_ids = {int(x["appid"]) for x in result["items"] if x.get("appid") is not None}
        for item in items:
            if len(result["items"]) >= limit:
                break
            aid = item.get("appid") if isinstance(item, dict) else None
            if aid is not None and int(aid) in seen_ids:
                continue
            if aid is not None:
                seen_ids.add(int(aid))
            result["items"].append(item)
        result["meta"]["source"] = "local+steam"
    except Exception as exc:
        result["meta"]["network"] = {"ok": False, "error": str(exc)}
    return result


def _lookup_game_name(appid: int) -> str:
    conn = _open_snapshot()
    if conn is None:
        return ""
    try:
        g = db.get_game(conn, int(appid))
        return str(g.get("name") or "").strip() if g else ""
    finally:
        conn.close()


def log_play(
    appid: int,
    status: Literal["played", "avoided", "wishlist"],
    note: str = "",
    scope: str = "default",
    name: str = "",
) -> dict:
    if status not in {"played", "avoided", "wishlist"}:
        return {"status": "error", "message": "status 必须是 played、avoided 或 wishlist。"}
    aid = int(appid)
    scope_key = scope or "default"
    game_name = str(name or "").strip() or _lookup_game_name(aid)
    now_ts = time.time()
    log = _load_log()
    bucket = log["scopes"].setdefault(scope_key, {"records": [], "deleted": {}})
    records = [r for r in bucket.get("records", []) if int(r.get("appid", 0)) != aid]
    records.append(
        {
            "appid": aid,
            "status": status,
            "name": game_name,
            "note": str(note or "").strip(),
            "updated_at": now_ts,
        }
    )
    bucket["records"] = records
    bucket.setdefault("deleted", {}).pop(str(aid), None)
    _save_log(log)
    return {
        "status": "saved",
        "appid": aid,
        "name": game_name,
        "scope": scope_key,
        "saved_status": status,
        "excluded": status in {"played", "avoided"},
        "excluded_appid": aid if status in {"played", "avoided"} else None,
    }


def unlog_play(appid: int, scope: str = "default") -> dict:
    aid = int(appid)
    scope_key = scope or "default"
    log = _load_log()
    bucket = log["scopes"].setdefault(scope_key, {"records": [], "deleted": {}})
    before = bucket.get("records", [])
    removed = [r for r in before if int(r.get("appid", 0)) == aid]
    if not removed:
        return {"status": "not_found", "appid": aid, "scope": scope_key}
    bucket["records"] = [r for r in before if int(r.get("appid", 0)) != aid]
    bucket.setdefault("deleted", {})[str(aid)] = time.time()
    _save_log(log)
    return {
        "status": "removed",
        "appid": aid,
        "name": removed[-1].get("name") or _lookup_game_name(aid),
        "scope": scope_key,
    }


def list_play_log(scope: str = "default") -> dict:
    scope_key = scope or "default"
    log = _load_log()
    records = sorted(
        _records(log, scope=scope_key),
        key=lambda r: float(r.get("updated_at") or 0.0),
        reverse=True,
    )
    return {"status": "ok", "scope": scope_key, "records": records}


def _demo_radar_markdown(items: list[dict], max_age_days: int | None, horror: str, players: int | None, mode: str, tag: str | None = None, widened: bool = False) -> str:
    badges = [
        f"`近{max_age_days}天`" if max_age_days and not widened else "`全周期潜力试玩`",
        f"`{_MODE_ZH.get(mode, mode)}`",
    ]
    if players:
        badges.append(f"`{players}人`")
    if horror and horror != "any":
        badges.append(f"`{_HORROR_ZH.get(horror, horror)}`")
    if tag:
        badges.append(f"`{tag}`")
    lines = [f"**🧪 潜力新 Demo 雷达**  {' '.join(badges)}"]
    if not items:
        lines.extend(["", "当前筛选条件下暂无新上架的高潜力 Demo，试试放宽人数或恐怖条件。"])
        return "\n".join(lines)
    for i, g in enumerate(items, 1):
        age = g.get("release_age_days")
        meta_parts = [f"`上线{age}天`" if age is not None else "`热门试玩`"]
        if g.get("max_players"):
            meta_parts.append(f"`最多{g['max_players']}人`")
        meta_parts.append(f"潜力分 **{float(g.get('potential_score') or 0):.1f}**")
        lines.append("")
        lines.append(f"**{i}. {_fmt_title(g['name'])}**  {' · '.join(meta_parts)}")

        signal_parts: list[str] = []
        ccu = int(g.get("current_players") or 0)
        fol = int(g.get("followers") or 0)
        d_ccu = int(g.get("delta_ccu") or 0)
        d_fol = int(g.get("delta_followers") or 0)
        news = int(g.get("recent_news_count") or 0)
        if ccu > 0:
            ccu_txt = f"在线 **{ccu}** 人"
            if d_ccu >= 15:
                ccu_txt += f"（↑+{d_ccu}）"
            signal_parts.append(ccu_txt)
        if fol > 0:
            est_low = fol * 7
            est_high = fol * 12
            if d_fol >= 10:
                fol_txt = f"关注 **{fol}** 人（↑+{d_fol}/天）"
            elif est_low >= 1000:
                fol_txt = f"关注 **{fol}** 人（约 {est_low / 1000:.1f}k–{est_high / 1000:.1f}k 愿望单）"
            else:
                fol_txt = f"关注 **{fol}** 人"
            signal_parts.append(fol_txt)
        if news > 0:
            signal_parts.append(f"近14天更新 {news} 次")
        if g.get("has_schinese"):
            signal_parts.append("简中")
        if g.get("has_trailer"):
            signal_parts.append("含预告")
        if g.get("has_ai_disclosure"):
            signal_parts.append("⚠️AI披露")
        if signal_parts:
            lines.append(f"📈 {' · '.join(signal_parts)}")

        target_id = g.get("demo_appid") or g["appid"]
        steam_url = f"https://store.steampowered.com/app/{target_id}/"
        desc = _truncate(g.get("short_desc_zh") or g.get("short_desc"), 72)
        if desc:
            lines.append(f"{desc}  [去试玩]({steam_url})")
        else:
            lines.append(f"🔗 [前往 Steam 试玩]({steam_url})")
    lines.extend(["", "💡 直接回 `第2个不好玩，推荐点别的` / `换一批` 自动记录并换新"])
    return "\n".join(lines)


def discover_potential_demos(
    max_age_days: int = 14,
    horror: Literal["none", "mild", "scary", "any"] = "any",
    players: Optional[int] = None,
    mode: Literal["online", "local", "any"] = "online",
    tag: Optional[str] = None,
    min_score: float = 8.0,
    limit: int = 6,
    scope: str = "default",
    extra_excluded: Optional[list[int]] = None,
) -> dict:
    """发现刚上线（1–14天）或处于试玩阶段的高潜力小而美 Demo，按实时在线/本体关注/日增速综合排序。"""
    log = _load_log()
    excluded = _excluded(log, scope=scope)
    if extra_excluded:
        excluded |= {int(x) for x in extra_excluded if str(x).isdigit()}
    conn = _open_snapshot()
    if conn is None:
        return {
            "status": "empty_snapshot",
            "message": "本地游戏快照不存在或无法读取，请等待 GitHub Actions 生成 data/games.sqlite。",
            "items": [],
            "markdown_preview": "",
        }
    try:
        rows = db.recommend_potential_demos(
            conn,
            max_age_days=max_age_days,
            horror=horror,
            players=players,
            mode=mode,
            tag=tag,
            min_score=min_score,
            limit=max(limit * 3, 24),
        )
        widened = False
        rows = _dedupe_demo_pairs([r for r in rows if int(r.get("appid", 0)) not in excluded])
        if not rows:
            widened = True
            rows = db.recommend_potential_demos(
                conn,
                max_age_days=None,
                horror=horror,
                players=players,
                mode=mode,
                tag=tag,
                min_score=0.0,
                limit=max(limit * 3, 24),
            )
            rows = _dedupe_demo_pairs([r for r in rows if int(r.get("appid", 0)) not in excluded])
        items = [_format_game(r, conn) for r in rows[: max(1, int(limit))]]
        return {
            "status": "ok",
            "items": items,
            "query": {
                "max_age_days": max_age_days,
                "horror": horror,
                "players": players,
                "mode": mode,
                "tag": tag,
                "min_score": min_score,
            },
            "meta": {
                "source": "snapshot",
                "count": len(items),
                "widened": widened,
                "excluded_appids": sorted(excluded),
            },
            "markdown_preview": _demo_radar_markdown(items, max_age_days, horror, players, mode, tag=tag, widened=widened),
        }
    finally:
        conn.close()


def tonight_playbook() -> str:
    return (
        "请最多用一轮回复询问四个问题：人数、恐怖接受度、预算（免费/Demo、50元以内或不限）和游玩时长（如有偏好类型如独立/生存也可一并传入 tag）；"
        "随后调用 recommend_coop_games 并设置 confirmed=True，优先展示有 Steam 鉴赏家精选背书的游戏、星级、鉴赏家/玩家双轨理由和避雷榜。"
    )


if mcp is not None:
    recommend_coop_games = mcp.tool()(recommend_coop_games)
    discover_potential_demos = mcp.tool()(discover_potential_demos)
    get_game_card = mcp.tool()(get_game_card)
    resolve_game = mcp.tool()(resolve_game)
    log_play = mcp.tool()(log_play)
    unlog_play = mcp.tool()(unlog_play)
    list_play_log = mcp.tool()(list_play_log)
    tonight_playbook = mcp.prompt()(tonight_playbook)


def main() -> None:
    if mcp is None:
        raise RuntimeError("FastMCP is unavailable. Install the optional MCP dependency with `pip install 'mcp[cli]'`.") from _MCP_IMPORT_ERROR
    mcp.run()


if __name__ == "__main__":
    main()
