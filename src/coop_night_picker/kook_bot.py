"""KOOK WebSocket + REST Poller Bot integration for coop-night-picker."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

import httpx

from . import server

KOOK_API_BASE = "https://www.kookapp.cn/api/v3"
KOOK_CONTENT_LIMIT = 1800
RETRY_BASE_DELAY = 2.0
PENDING_TTL_SECONDS = 600.0

_HORROR_NEG_RE = re.compile(r"(?:不|别|勿|不要|不想|怕|拒绝|非)[^，。,！!？?\n]{0,4}(?:恐怖|吓人|惊悚)")
_PAID_NOT_FREE_RE = re.compile(r"(?:不要免费|不玩免费|别免费|非免费|付费|买断|花钱)")
_PRICE_CAP_RE = re.compile(r"(\d{1,4})\s*(?:元|块|rmb)?\s*(?:以内|以下|内|左右)")
_POTENTIAL_DEMO_RE = re.compile(
    r"(?:潜力\s*(?:demo|试玩|新游|小游戏)|小而美|新\s*demo|最新\s*demo|刚出.{0,6}(?:demo|试玩)|demo\s*雷达|新试玩|挖宝)",
    re.I,
)


def _coerce_players(value: Any, default: int = 4) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return max(2, min(16, int(value)))
    nums = re.findall(r"\d+", str(value))
    if nums:
        return max(2, min(16, int(nums[-1])))
    return default


def split_reply_chunks(text: str, limit: int = KOOK_CONTENT_LIMIT) -> list[str]:
    """Split a long KMarkdown reply into sendable chunks, preferring line boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks

_TAG_KEYWORDS = {
    "独立": "独立",
    "indie": "独立",
    "demo": "demo",
    "试玩": "demo",
    "新品": "demo",
    "生存": "生存",
    "解谜": "解谜",
    "肉鸽": "肉鸽",
    "roguelike": "肉鸽",
    "沙盒": "沙盒",
    "射击": "射击",
    "欢乐": "欢乐",
    "搞笑": "欢乐",
    "整活": "欢乐",
    "妖精": "欢乐",
    "抽象": "欢乐",
    "派对": "派对",
    "物理": "物理",
    "友尽": "欢乐",
}


_BOT_DISPLAY_MENTION_RE = re.compile(r"@What\s+to\s+play\b", re.I)
_UNMENTIONED_CMD_RE = re.compile(
    r"(?:今晚玩什么|今晚玩啥|开黑玩什么|开黑玩啥|推荐联机|推荐个联机|联机游戏推荐|"
    r"潜力\s*(?:demo|试玩|新游|小游戏)|小而美|新\s*demo|最新\s*demo|demo\s*雷达|新试玩|挖宝|"
    r"^/(?:start|help)\b|^搜\s+\S+|^详情\s*\d{3,10}\b|^(?:玩过|避雷|拉黑|想玩)\s*\d{3,10}\b|"
    r"^(?:已记录|我的记录|玩过列表|避雷名单|已排除|撤销|换一批)$)",
    re.I,
)
_FOLLOWUP_FEEDBACK_RE = re.compile(
    r"(?:不好玩|没意思|不想玩|不喜欢|不感兴趣|别推|劝退|无聊|玩过|已玩|打过|通关|"
    r"换一批|推荐点?别的|换点别的|换一个|换几个|再推几个|来点别的|撤销|撤回|取消排除|取消避雷)",
    re.I,
)
_REROLL_WORDS_RE = re.compile(r"(?:换一批|推荐点?别的|换点别的|换一个|换几个|再推|来点别的|其他游戏|别的吧)", re.I)
_BATCH_TARGET_RE = re.compile(r"(?:这些|这几个|这批|全都|全部|都玩过|都不好玩|都不喜欢|都不想玩|都别推|都没意思)")
_PLAYED_WORDS_RE = re.compile(r"(?:玩过了?|已玩|打过了?|通关了?|通了)")
_DISLIKE_WORDS_RE = re.compile(r"(?:不好玩|没意思|不想玩|不喜欢|不感兴趣|别推了?|避雷|拉黑|劝退|无聊|\bpass\b)", re.I)
_WISHLIST_WORDS_RE = re.compile(r"(?:收藏|加愿望单)")
_LIST_LOG_RE = re.compile(r"^(?:查看|看看)?(?:已记录|我的记录|玩过列表|避雷名单|已排除|排除列表|记录列表)$")
_UNDO_RE = re.compile(r"^(?:撤销|撤回|恢复|取消排除|取消避雷|取消玩过)(?:\s+(.+))?$")
_CN_IDX_MAP = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}
_FEEDBACK_TTL_SECONDS = 900.0
_BARE_GREETINGS = {"今晚玩什么", "今晚玩啥", "玩什么", "玩啥", "推荐", "在吗", "说话", "/start", "/help", "你好", "hi", "hello"}
_VALID_HORROR = {"none", "mild", "scary", "any"}
_VALID_BUDGET = {"free_or_demo", "under_50", "any_paid", "any"}
_VALID_MODE = {"online", "local", "any"}


def _strip_mentions(text: str, bot_name: str | None = None) -> str:
    cleaned = re.sub(r"\((?:met|rol)\)\d+\((?:met|rol)\)", " ", text or "")
    if bot_name:
        cleaned = re.sub(rf"@{re.escape(bot_name)}\b", " ", cleaned, flags=re.I)
    cleaned = _BOT_DISPLAY_MENTION_RE.sub(" ", cleaned)
    cleaned = re.sub(r"@\S+", " ", cleaned)
    return " ".join(cleaned.split()).strip()


def _parse_feedback_intent(cleaned: str, recent_recs: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Parse natural-language feedback ('第2个不好玩推荐点别的', '浣熊模拟器玩过了', '换一批', '撤销', '已记录')."""
    if not cleaned:
        return None
    if _LIST_LOG_RE.match(cleaned):
        return {"action": "list_log"}
    m_undo = _UNDO_RE.match(cleaned)
    if m_undo:
        return {"action": "undo", "target_text": (m_undo.group(1) or "").strip()}

    # Strip filter-condition negations ("不想玩恐怖", "不玩免费") so they don't look like game dislikes
    probe = _HORROR_NEG_RE.sub(" ", cleaned)
    probe = _PAID_NOT_FREE_RE.sub(" ", probe)

    has_played = bool(_PLAYED_WORDS_RE.search(probe))
    has_dislike = bool(_DISLIKE_WORDS_RE.search(probe))
    has_wishlist = bool(_WISHLIST_WORDS_RE.search(probe))
    wants_reroll = bool(_REROLL_WORDS_RE.search(probe))
    wants_batch_skip = "换一批" in cleaned or bool(_BATCH_TARGET_RE.search(cleaned))

    if not (has_played or has_dislike or has_wishlist or wants_reroll):
        return None

    # Neutral reroll with no played/dislike sentiment ("换一批", "推荐点别的")
    if wants_reroll and not (has_played or has_dislike or has_wishlist):
        return {"action": "reroll", "batch_skip": True}

    status = "played" if has_played else ("avoided" if has_dislike else "wishlist")
    recs = list(recent_recs or [])
    is_batch = bool(_BATCH_TARGET_RE.search(cleaned))

    # 1. Extract explicit 1-based indices ("第2个", "2不好玩", "第一个和第三个")
    indices: list[int] = []
    for m in re.finditer(r"第\s*([1-6一二两三四五六])\s*[个号]?", cleaned):
        raw = m.group(1)
        idx = int(raw) if raw.isdigit() else _CN_IDX_MAP.get(raw, 0)
        if 1 <= idx <= 6 and idx not in indices:
            indices.append(idx)
    if not indices:
        m_lead = re.match(r"^([1-6])\s*[个号]?\s*(?:不好玩|玩过|已玩|没意思|不想玩|不喜欢|避雷|拉黑|别推|不感兴趣|pass)", cleaned, re.I)
        if m_lead:
            indices.append(int(m_lead.group(1)))

    # 2. Extract explicit AppID if present
    m_aid = re.search(r"\b(\d{4,10})\b", cleaned)
    explicit_appid = int(m_aid.group(1)) if m_aid else None

    # 3. Extract candidate game name by stripping feedback / reroll / punctuation noise
    name_guess = cleaned
    for pat in (
        _REROLL_WORDS_RE,
        _BATCH_TARGET_RE,
        _PLAYED_WORDS_RE,
        _DISLIKE_WORDS_RE,
        _WISHLIST_WORDS_RE,
        re.compile(r"第\s*[1-6一二两三四五六]\s*[个号]?"),
        re.compile(r"(?:这个|那个|这款|那款|这游戏|这个游戏|游戏|感觉|觉得|好像|有点|太|很|真的|已经|我们|我都|都|也|就|把|给|我|吧|啊|呢|了|的)"),
    ):
        name_guess = pat.sub(" ", name_guess)
    name_guess = re.sub(r"[《》「」『』\[\]()（）,，。!！?？、:：;；~～]+", " ", name_guess)
    name_guess = " ".join(name_guess.split()).strip()

    return {
        "action": "feedback",
        "status": status,
        "indices": indices,
        "is_batch": is_batch,
        "explicit_appid": explicit_appid,
        "name_guess": name_guess,
        "wants_reroll": wants_reroll,
        "wants_batch_skip": wants_batch_skip,
        "raw_text": cleaned,
    }


def parse_intent_from_text(
    text: str,
    defaults: dict[str, Any] | None = None,
    bot_name: str | None = None,
    recent_recs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministically parse KOOK user message into tool intent & parameters."""
    cleaned = _strip_mentions(text, bot_name=bot_name)
    low = cleaned.lower()

    m_card = re.search(r"(?:详情|卡片|card)\s*(\d{3,10})", low)
    if m_card:
        return {"action": "card", "appid": int(m_card.group(1))}

    m_log = re.search(r"^(玩过|已玩|避雷|拉黑|想玩)\s*(\d{3,10})$", cleaned)
    if m_log:
        word, aid = m_log.group(1), int(m_log.group(2))
        status = "played" if word in {"玩过", "已玩"} else ("avoided" if word in {"避雷", "拉黑"} else "wishlist")
        return {"action": "log", "appid": aid, "status": status}

    m_search = re.search(r"^(?:搜|搜索|查|查找)\s+(.+)$", cleaned)
    if m_search:
        return {"action": "search", "query": m_search.group(1).strip()}

    fb = _parse_feedback_intent(cleaned, recent_recs=recent_recs)
    if fb is not None:
        return fb

    params: dict[str, Any] = dict(defaults or {})

    # Players
    _cn_num = {"二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
    m_players = re.search(r"(\d{1,2})\s*(?:个?人|p|players?)", low)
    m_players_cn = re.search(r"([二两三四五六七八])\s*个?人", cleaned)
    if m_players:
        params["players"] = max(2, min(16, int(m_players.group(1))))
    elif m_players_cn:
        params["players"] = _cn_num[m_players_cn.group(1)]
    elif "双人" in cleaned or "两人" in cleaned or "情侣" in cleaned:
        params["players"] = 2
    elif "三人" in cleaned:
        params["players"] = 3
    elif "四人" in cleaned:
        params.setdefault("players", 4)
    elif "五人以上" in cleaned or "五个人以上" in cleaned:
        params.setdefault("players", 5)

    # Horror
    if (
        any(w in cleaned for w in ("不恐怖", "不要恐怖", "别恐怖", "胆小", "一点都不行", "非恐怖"))
        or _HORROR_NEG_RE.search(cleaned)
    ):
        params["horror"] = "none"
    elif any(w in cleaned for w in ("微恐", "心理恐怖", "一点点恐怖")):
        params["horror"] = "mild"
    elif any(w in cleaned for w in ("恐怖", "吓人", "越吓人越好", "惊悚")):
        params["horror"] = "scary"
    elif any(w in cleaned for w in ("都行", "无所谓", "随便", "不限")):
        params.setdefault("horror", "any")

    # Budget
    m_price_cap = _PRICE_CAP_RE.search(low)
    if _PAID_NOT_FREE_RE.search(cleaned):
        params["budget"] = "any_paid"
    elif m_price_cap:
        cap_val = int(m_price_cap.group(1))
        if cap_val == 0:
            params["budget"] = "free_or_demo"
        elif cap_val <= 50:
            params["budget"] = "under_50"
        else:
            params["budget"] = "any"
    elif any(w in low for w in ("50", "五十", "便宜", "平价")):
        params["budget"] = "under_50"
    elif any(w in low for w in ("免费", "白嫖", "demo", "试玩", "0元")):
        params["budget"] = "free_or_demo"
    elif any(w in cleaned for w in ("价格无所谓", "不差钱", "预算不限", "随便", "不限", "都行")):
        params.setdefault("budget", "any")

    # Mode
    if any(w in cleaned for w in ("本地", "同屏", "分屏")):
        params["mode"] = "local"
    else:
        params.setdefault("mode", "online")

    # Tag
    for kw, canonical in _TAG_KEYWORDS.items():
        if kw in low:
            if canonical == "demo" and params.get("budget") == "under_50":
                continue
            params["tag"] = canonical
            break

    # Duration
    if any(w in cleaned for w in ("短局", "半小时", "30分钟")):
        params["session_duration"] = "short"
    elif any(w in cleaned for w in ("一两个小时", "两小时", "一晚")):
        params["session_duration"] = "medium"
    elif any(w in cleaned for w in ("通宵", "不限时", "长期")):
        params["session_duration"] = "long"

    if _POTENTIAL_DEMO_RE.search(cleaned):
        if params.get("tag") == "demo":
            params.pop("tag", None)
        return {"action": "potential_demos", "params": params}

    # Recommend immediately whenever the user gives ANY concrete condition; only ask when 0 conditions were supplied.
    has_any_condition = (
        any(params.get(key) not in (None, "") for key in ("players", "horror", "budget", "tag", "session_duration"))
        or params.get("mode") == "local"
    )
    if has_any_condition:
        params.setdefault("players", 4)
        params.setdefault("horror", "any")
        params.setdefault("budget", "any")
        return {"action": "recommend", "params": params, "confirmed": True}

    return {"action": "ask", "params": params}


def call_llm_for_params(user_text: str) -> dict[str, Any] | None:
    """Optional LLM parser using free GitHub Models (GITHUB_TOKEN) or custom LLM_API_KEY."""
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GITHUB_TOKEN")
    if not api_key:
        return None
    base_url = (os.getenv("LLM_BASE_URL") or "https://models.github.ai/inference").rstrip("/")
    default_model = "openai/gpt-4o-mini" if "models.github.ai" in base_url else "gpt-4o-mini"
    model = os.getenv("LLM_MODEL") or default_model
    prompt = (
        "将用户的联机游戏需求解析为JSON对象，字段仅限："
        '{"horror":"none|mild|scary|any|null","budget":"free_or_demo|under_50|any|null",'
        '"players":2~8或null,"mode":"online|local","tag":"独立|demo|生存|解谜|肉鸽|沙盒|射击|欢乐|null"}。'
        "只输出纯JSON，不要代码块。"
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 120,
                },
            )
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                m = re.search(r"\{.*\}", content, re.S)
                if m:
                    return json.loads(m.group(0))
    except Exception:
        return None
    return None


class KookBotSession:
    """Stateful KOOK channel coordinator with rate limiting and feedback memory."""

    def __init__(self, cooldown_sec: float = 1.5, pending_ttl_sec: float = PENDING_TTL_SECONDS, bot_name: str | None = None):
        self.pending_channels: dict[str, dict[str, Any]] = {}
        self.pending_since: dict[str, float] = {}
        self.last_reply_at: dict[str, float] = {}
        self.last_recommendations: dict[str, list[dict[str, Any]]] = {}
        self.last_rec_at: dict[str, float] = {}
        self.last_query: dict[str, dict[str, Any]] = {}
        self.session_shown: dict[str, set[int]] = {}
        self.seen_games_cache: dict[str, dict[int, str]] = {}
        self.cooldown_sec = cooldown_sec
        self.pending_ttl_sec = pending_ttl_sec
        self.bot_name = bot_name

    def _active_recent_recs(self, channel_id: str, now: float) -> list[dict[str, Any]]:
        recs = self.last_recommendations.get(channel_id) or []
        if recs and now - self.last_rec_at.get(channel_id, 0.0) <= _FEEDBACK_TTL_SECONDS:
            return recs
        return []

    def _remember_recommendations(
        self,
        channel_id: str,
        now: float,
        action: str,
        params: dict[str, Any],
        res: dict[str, Any],
        *,
        reset_session_shown: bool = False,
    ) -> list[dict[str, Any]]:
        if action == "potential_demos":
            raw_items = res.get("items") or []
        else:
            raw_items = res.get("recommended") or [
                g for values in (res.get("groups") or {}).values() for g in values
            ]
        compact = [
            {"appid": int(g["appid"]), "name": str(g.get("name") or "").strip()}
            for g in raw_items
            if isinstance(g, dict) and g.get("appid") is not None
        ]
        if compact:
            self.last_recommendations[channel_id] = compact
            self.last_rec_at[channel_id] = now
            self.last_query[channel_id] = {"action": action, "params": dict(params)}
            cache = self.seen_games_cache.setdefault(channel_id, {})
            for item in compact:
                cache[item["appid"]] = item["name"]
        if reset_session_shown:
            self.session_shown[channel_id] = set()
        return compact

    def _run_query(
        self,
        channel_id: str,
        scope_key: str,
        now: float,
        action: str,
        params: dict[str, Any],
        *,
        extra_excluded: set[int] | None = None,
        reset_session_shown: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        extra_list = sorted(extra_excluded) if extra_excluded else None
        if action == "potential_demos":
            res = server.discover_potential_demos(
                max_age_days=14,
                horror=params.get("horror", "any"),
                players=_coerce_players(params.get("players"), 0) if params.get("players") else None,
                mode=params.get("mode", "online"),
                tag=params.get("tag"),
                limit=6,
                scope=scope_key,
                extra_excluded=extra_list,
            )
        else:
            res = server.recommend_coop_games(
                horror=params.get("horror", "any"),
                budget=params.get("budget", "any"),
                players=_coerce_players(params.get("players"), 4),
                mode=params.get("mode", "online"),
                tag=params.get("tag"),
                confirmed=True,
                limit_per_tier=3,
                scope=scope_key,
                extra_excluded=extra_list,
            )
        shown = self._remember_recommendations(
            channel_id, now, action, params, res, reset_session_shown=reset_session_shown
        )
        return res, shown

    def _resolve_feedback_targets(
        self,
        channel_id: str,
        intent: dict[str, Any],
        recent_recs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if intent.get("is_batch") and recent_recs:
            return list(recent_recs)

        indices = intent.get("indices") or []
        if indices and recent_recs:
            picked = [recent_recs[i - 1] for i in indices if 1 <= i <= len(recent_recs)]
            if picked:
                return picked

        aid = intent.get("explicit_appid")
        seen_cache = self.seen_games_cache.get(channel_id) or {}
        if aid:
            return [{"appid": int(aid), "name": seen_cache.get(int(aid), "")}]

        raw_low = str(intent.get("raw_text") or "").lower()
        name_guess = str(intent.get("name_guess") or "").strip()
        name_guess_low = name_guess.lower()

        # Match against recently recommended or previously seen games in this channel
        candidates = list(recent_recs) + [
            {"appid": k, "name": v}
            for k, v in seen_cache.items()
            if all(r["appid"] != k for r in recent_recs)
        ]
        for rec in candidates:
            rname = str(rec.get("name") or "").strip()
            if not rname:
                continue
            r_low = rname.lower()
            r_base = re.sub(r"\s*demo\s*$", "", r_low).strip()
            if (len(r_low) >= 2 and r_low in raw_low) or (len(r_base) >= 2 and r_base in raw_low):
                return [rec]
            if name_guess_low and (name_guess_low in r_low or r_low in name_guess_low):
                return [rec]

        # Lookup named game in local DB / Steam search
        if len(name_guess) >= 2:
            res = server.resolve_game(name_guess, limit=1)
            items = res.get("items") or []
            if items and items[0].get("appid"):
                return [{"appid": int(items[0]["appid"]), "name": str(items[0].get("name") or name_guess)}]

        # Pronoun fallback ("这个不好玩，推荐点别的") when we just recommended a list
        if recent_recs:
            return [recent_recs[0]]
        return []

    def handle_message(
        self,
        channel_id: str,
        text: str,
        is_mentioned: bool = True,
        scope: str | None = None,
    ) -> str | None:
        now = time.time()
        scope_key = scope or "default"
        if channel_id in self.pending_channels:
            since = self.pending_since.get(channel_id, now)
            if now - since > self.pending_ttl_sec:
                self.pending_channels.pop(channel_id, None)
                self.pending_since.pop(channel_id, None)
        in_pending = channel_id in self.pending_channels
        recent_recs = self._active_recent_recs(channel_id, now)
        stripped = (text or "").strip()
        if re.search(r"\((?:met|rol)\)\d+\((?:met|rol)\)", stripped) or _BOT_DISPLAY_MENTION_RE.search(stripped):
            is_mentioned = True
        is_followup_feedback = bool(recent_recs) and bool(_FOLLOWUP_FEEDBACK_RE.search(stripped))
        if not is_mentioned and not in_pending and not is_followup_feedback and not _UNMENTIONED_CMD_RE.search(stripped):
            return None
        # Cooldown only guards unsolicited spam; while a dialog or follow-up is open the user is answering us.
        if not in_pending and not is_followup_feedback and now - self.last_reply_at.get(channel_id, 0.0) < self.cooldown_sec:
            return None

        # Fresh @mention starts a clean query instead of leaking stale filters from an earlier dialog.
        prior = None if is_mentioned else self.pending_channels.get(channel_id)
        intent = parse_intent_from_text(stripped, defaults=prior, bot_name=self.bot_name, recent_recs=recent_recs)

        if intent["action"] == "card":
            self.last_reply_at[channel_id] = now
            card = server.get_game_card(intent["appid"])
            if card.get("status") != "ok":
                return f"未在库中找到 AppID `{intent['appid']}`，可以用 `搜 游戏名` 先查一下。"
            if card["is_free"]:
                price_str = "`免费`"
            elif card.get("price_cny"):
                price_str = f"`¥{float(card['price_cny']):g}`"
                if card.get("has_demo"):
                    price_str += " `含Demo`"
            elif card.get("has_demo"):
                price_str = "`免费试玩Demo`"
            else:
                price_str = "`待定`"

            meta = [price_str]
            if card.get("max_players"):
                meta.append(f"`最多{card['max_players']}人`")
            pos_str = f"{float(card.get('positive_pct') or 0):.1f}%"
            review_line = f"{card['stars']} {card['review_tier']}（好评 {pos_str}"
            if card.get("schinese_pct") is not None:
                review_line += f"｜简中 {float(card['schinese_pct']):.1f}%"
            review_line += "）"

            lines = [
                f"**🎮 {server._fmt_title(card['name'])}**  {' · '.join(meta)}  `AppID: {card['appid']}`",
                f"📊 {review_line}",
            ]
            if card.get("current_players") or card.get("followers"):
                early = []
                if card.get("current_players"):
                    early.append(f"在线 **{card['current_players']}** 人")
                if card.get("followers"):
                    early.append(f"关注 **{card['followers']}** 人")
                lines.append(f"📈 {' · '.join(early)}")
            human_curators = [c for c in (card.get("curators") or []) if not str(c).startswith("Steam")]
            if human_curators:
                lines.append(f"🏅 鉴赏家推荐：{'、'.join(human_curators[:4])}")
            quotes = card.get("positive_quotes") or []
            if quotes:
                lines.append(f"> {server._truncate(quotes[0].get('quote_text'), 110)}")
            lines.append(f"🔗 [Steam 商店页](https://store.steampowered.com/app/{card['appid']}/)")
            return "\n".join(lines)

        if intent["action"] == "search":
            self.last_reply_at[channel_id] = now
            res = server.resolve_game(intent["query"], limit=5)
            items = res.get("items") or []
            if not items:
                return f"没有搜到与「{intent['query']}」相关的联机游戏。"
            lines = [f"🔎 **「{intent['query']}」搜索结果**"]
            for it in items[:5]:
                aid = it.get("appid")
                tier_label = it.get("review_tier_zh") or "Steam 商店"
                lines.append(f"• **{server._fmt_title(it.get('name', ''))}**  `{aid}` · {tier_label}  [商店页](https://store.steampowered.com/app/{aid}/)")
            lines.append("\n💡 发送 `@我 详情 <AppID>` 看评测卡，`@我 玩过 <AppID>` 排除该游戏。")
            return "\n".join(lines)

        if intent["action"] == "log":
            self.last_reply_at[channel_id] = now
            saved = server.log_play(intent["appid"], intent["status"], scope=scope_key)
            label = {"played": "已玩过（后续自动排除）", "avoided": "已避雷（后续自动排除）", "wishlist": "想玩清单"}[intent["status"]]
            title = server._fmt_title(saved["name"]) if saved.get("name") else f"AppID `{intent['appid']}`"
            return f"✅ 已云端记录 **{title}** 为 **{label}**！（发 `撤销` 可撤回）"

        if intent["action"] == "list_log":
            self.last_reply_at[channel_id] = now
            recs = server.list_play_log(scope=scope_key).get("records") or []
            if not recs:
                return "📋 本群还没有已玩过或不想玩的记录。推荐结果后直接回复 `第2个不好玩，推荐点别的` 或 `玩过 游戏名` 就能自动云端记录！"
            status_badge = {"played": "✅ 已玩过", "avoided": "🚫 不感兴趣", "wishlist": "⭐ 想玩"}
            lines = [f"📋 **本群云端记录（共 {len(recs)} 款）**"]
            for r in recs[:15]:
                name = server._fmt_title(r["name"]) if r.get("name") else f"AppID {r['appid']}"
                lines.append(f"• {status_badge.get(r.get('status', ''), r.get('status', ''))}  **{name}**  `{r['appid']}`")
            if len(recs) > 15:
                lines.append(f"…以及另外 {len(recs) - 15} 款")
            lines.append("\n💡 发 `撤销` 撤回最新一条，或 `取消排除 <AppID/游戏名>` 恢复推荐。")
            return "\n".join(lines)

        if intent["action"] == "undo":
            self.last_reply_at[channel_id] = now
            target_text = str(intent.get("target_text") or "").strip()
            recs = server.list_play_log(scope=scope_key).get("records") or []
            if not recs:
                return "当前没有可撤销的记录。"
            target_rec = None
            if not target_text:
                target_rec = recs[0]
            else:
                m_id = re.search(r"\d{3,10}", target_text)
                if m_id:
                    want_id = int(m_id.group(0))
                    target_rec = next((r for r in recs if int(r["appid"]) == want_id), None)
                if target_rec is None:
                    tl = target_text.lower()
                    target_rec = next(
                        (r for r in recs if r.get("name") and (tl in r["name"].lower() or r["name"].lower() in tl)),
                        None,
                    )
            if target_rec is None:
                return f"没有在已记录清单中找到「{target_text}」，发 `已记录` 可查看全部记录。"
            res_undo = server.unlog_play(int(target_rec["appid"]), scope=scope_key)
            name = server._fmt_title(res_undo["name"]) if res_undo.get("name") else f"AppID `{target_rec['appid']}`"
            return f"↩️ 已撤销 **{name}**（`{target_rec['appid']}`）的记录，后续可重新推荐。"

        if intent["action"] == "reroll":
            self.last_reply_at[channel_id] = now
            last_q = self.last_query.get(channel_id)
            if not last_q:
                return "还没开始筛选呢，直接发一句条件就行（例如：`4人 不恐怖 50以内` 或 `潜力demo`）！"
            shown_set = self.session_shown.setdefault(channel_id, set())
            for r in recent_recs:
                shown_set.add(int(r["appid"]))
            res, shown = self._run_query(
                channel_id,
                scope_key,
                now,
                last_q["action"],
                last_q["params"],
                extra_excluded=shown_set,
                reset_session_shown=False,
            )
            if res.get("status") != "ok" or not shown or not res.get("markdown_preview"):
                self.session_shown[channel_id] = set()
                return "这组条件下的备选都看完啦！可以换个筛选条件，或者发 `@我 潜力demo` 挖刚上架的新试玩。"
            return res["markdown_preview"]

        if intent["action"] == "feedback":
            self.last_reply_at[channel_id] = now
            targets = self._resolve_feedback_targets(channel_id, intent, recent_recs)
            if not targets:
                return "没识别到具体哪款游戏，可以直接说 `第2个不好玩，推荐点别的` 或 `玩过 游戏名`。"
            status = intent["status"]
            note = "不好玩/不感兴趣" if status == "avoided" else ("群聊标记已玩" if status == "played" else "想玩")
            saved_names: list[str] = []
            for t in targets:
                saved = server.log_play(int(t["appid"]), status, note=note, scope=scope_key, name=t.get("name") or "")
                display = server._fmt_title(saved["name"]) if saved.get("name") else f"AppID `{t['appid']}`"
                saved_names.append(display)
            verb = {"played": "已玩过", "avoided": "不感兴趣", "wishlist": "想玩"}[status]
            if len(saved_names) == 1:
                confirm = f"📝 已云端记录 **{saved_names[0]}** 为 **{verb}**（以后不再推，发 `撤销` 可撤回）。"
            else:
                confirm = f"📝 已云端记录这 {len(saved_names)} 款（{'、'.join(saved_names[:4])}）为 **{verb}**。"

            last_q = self.last_query.get(channel_id)
            should_reroll = bool(last_q) and (intent.get("wants_reroll") or status == "avoided")
            if should_reroll and last_q:
                skip_ids = set(self.session_shown.get(channel_id, set()))
                if intent.get("wants_batch_skip"):
                    for r in recent_recs:
                        skip_ids.add(int(r["appid"]))
                    self.session_shown[channel_id] = set(skip_ids)
                res, shown = self._run_query(
                    channel_id,
                    scope_key,
                    now,
                    last_q["action"],
                    last_q["params"],
                    extra_excluded=skip_ids if skip_ids else None,
                    reset_session_shown=False,
                )
                if res.get("status") == "ok" and shown and res.get("markdown_preview"):
                    return f"{confirm}\n\n{res['markdown_preview']}"
                return f"{confirm}\n\n当前条件下暂时没有更多备选了，试试放宽条件或发 `@我 潜力demo`！"
            return confirm

        if intent["action"] == "potential_demos":
            self.pending_channels.pop(channel_id, None)
            self.pending_since.pop(channel_id, None)
            self.last_reply_at[channel_id] = now
            p = intent.get("params") or {}
            res, _ = self._run_query(
                channel_id,
                scope_key,
                now,
                "potential_demos",
                p,
                reset_session_shown=True,
            )
            if res.get("status") != "ok" or not res.get("markdown_preview"):
                return "当前筛选条件下暂无潜力新 Demo，试试直接发送 `@我 潜力demo` 查看全部！"
            return res["markdown_preview"]

        if intent["action"] == "ask":
            cleaned_for_llm = _strip_mentions(stripped, bot_name=self.bot_name)
            if len(cleaned_for_llm) > 6 and cleaned_for_llm.lower() not in _BARE_GREETINGS:
                llm_guess = call_llm_for_params(cleaned_for_llm)
                if isinstance(llm_guess, dict) and any(llm_guess.get(k) for k in ("horror", "budget", "tag", "players")):
                    p = intent["params"]
                    if llm_guess.get("horror") in _VALID_HORROR:
                        p["horror"] = llm_guess["horror"]
                    if llm_guess.get("budget") in _VALID_BUDGET:
                        p["budget"] = llm_guess["budget"]
                    if llm_guess.get("mode") in _VALID_MODE:
                        p["mode"] = llm_guess["mode"]
                    if llm_guess.get("players") not in (None, "null", ""):
                        p["players"] = _coerce_players(llm_guess["players"], 4)
                    if llm_guess.get("tag") not in (None, "null", ""):
                        p["tag"] = str(llm_guess["tag"]).strip()
                    p.setdefault("players", 4)
                    p.setdefault("horror", "any")
                    p.setdefault("budget", "any")
                    p.setdefault("mode", "online")
                    intent = {"action": "recommend", "params": p, "confirmed": True}

        if intent["action"] == "recommend":
            self.pending_channels.pop(channel_id, None)
            self.pending_since.pop(channel_id, None)
            self.last_reply_at[channel_id] = now
            p = intent["params"]
            res, _ = self._run_query(
                channel_id,
                scope_key,
                now,
                "recommend",
                p,
                reset_session_shown=True,
            )
            if res.get("status") != "ok" or not res.get("markdown_preview"):
                return "当前筛选条件下暂无匹配游戏，试试放宽条件（例如：`4人 都行 价格无所谓`）！"
            return res["markdown_preview"]

        self.pending_channels[channel_id] = intent.get("params") or {}
        self.pending_since[channel_id] = now
        self.last_reply_at[channel_id] = now
        params = intent.get("params") or {}
        missing: list[str] = []
        if not params.get("players"):
            missing.append("**人数**：2人 / 3-4人 / 5人以上")
        if not params.get("horror"):
            missing.append("**恐怖接受度**：不恐怖 / 微恐 / 越吓人越好 / 都行")
        if not params.get("budget"):
            missing.append("**预算**：免费或Demo / 50元以内 / 价格不限")
        if not missing:
            missing.append("**偏好标签（可选）**：独立 / Demo / 生存 / 解谜 / 肉鸽 / 沙盒 / 欢乐")
        header = "🎮 **今晚玩什么 · 还差一点信息**" if in_pending else "🎮 **今晚玩什么 · 开黑选游助手**"
        numbered = "\n".join(f"{i}. {item}" for i, item in enumerate(missing, 1))
        return f"{header}\n直接回一句就行（不用再次@我），例如 `4人 不恐怖 50以内`：\n{numbered}"


async def _kook_get_json(
    http: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    attempts: int = 5,
    deadline: float | None = None,
) -> dict[str, Any]:
    """GET a KOOK v3 API endpoint with exponential backoff for cross-border timeouts and 429/5xx."""
    last_exc: Exception | None = None
    url = f"{KOOK_API_BASE}/{path.lstrip('/')}"
    for attempt in range(max(1, attempts)):
        if deadline is not None and time.time() >= deadline:
            break
        try:
            resp = await http.get(url, params=params)
            status = getattr(resp, "status_code", 200)
            if status == 429:
                headers = getattr(resp, "headers", {}) or {}
                retry_after = float(headers.get("X-Rate-Limit-Reset") or headers.get("Retry-After") or (RETRY_BASE_DELAY * (2**attempt)))
                delay = min(30.0, max(RETRY_BASE_DELAY, retry_after))
                if deadline is not None:
                    delay = min(delay, max(0.0, deadline - time.time()))
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            if status >= 500:
                raise RuntimeError(f"KOOK HTTP {status} for {path}")
            body = resp.json()
            if not isinstance(body, dict):
                raise RuntimeError(f"Unexpected KOOK JSON payload for {path}")
            return body
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            delay = min(30.0, RETRY_BASE_DELAY * (2**attempt))
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                delay = min(delay, remaining)
            print(f"KOOK GET {path} attempt {attempt + 1}/{attempts} failed ({type(exc).__name__}: {exc}); retrying in {delay:.1f}s", flush=True)
            await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"KOOK GET {path} exhausted before deadline")


async def run_kook_ws(token: str, max_runtime_seconds: float = 19800) -> None:
    """Connect to KOOK WebSocket gateway with automatic reconnection + gentle REST fallback."""
    import websockets

    headers = {"Authorization": f"Bot {token}"}
    started_at = time.time()
    deadline = started_at + max_runtime_seconds
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as http:
        me_data: dict[str, Any] | None = None
        while time.time() < deadline:
            try:
                me_data = await _kook_get_json(http, "/user/me", attempts=4, deadline=deadline)
                break
            except Exception as exc:
                remaining = deadline - time.time()
                if remaining <= 0:
                    print(f"KOOK auth unavailable before runtime window ended: {exc}", flush=True)
                    return
                delay = min(30.0, RETRY_BASE_DELAY * 2, remaining)
                print(f"KOOK auth network retry in {delay:.1f}s: {exc}", flush=True)
                await asyncio.sleep(delay)

        if not me_data:
            return
        if me_data.get("code") != 0:
            raise RuntimeError(f"KOOK auth failed: code={me_data.get('code')} msg={me_data.get('message')}")
        bot_id = str(me_data["data"]["id"])
        bot_name = me_data["data"].get("username", "Bot")
        print(f"KOOK bot authenticated: {bot_name} (id={bot_id})", flush=True)

        session = KookBotSession(bot_name=bot_name)
        seen_msg_ids: set[str] = set()
        max_message_age_seconds = int(os.getenv("KOOK_MAX_MESSAGE_AGE_SECONDS", "900"))
        ws_connected = False

        # Pull cloud-persisted play/dislike logs from GitHub bot-state branch on startup
        try:
            synced = await asyncio.to_thread(server.sync_cloud_user_log)
            if synced:
                print("Synced cloud play/dislike log from GitHub bot-state branch", flush=True)
        except Exception as exc:
            print(f"Cloud user log sync warning: {exc}", flush=True)

        # Discover text channels and snapshot existing message IDs on startup (without pre-warming pending dialogs)
        text_channels: list[str] = []
        channel_to_guild: dict[str, str] = {}
        try:
            g_data = await _kook_get_json(http, "/guild/list", attempts=2, deadline=deadline)
            for g in g_data.get("data", {}).get("items", []):
                gid = str(g.get("id") or "")
                try:
                    c_data = await _kook_get_json(http, "/channel/list", params={"guild_id": g["id"]}, attempts=2, deadline=deadline)
                except Exception as exc:
                    print(f"Channel list warning guild={g.get('id')}: {exc}", flush=True)
                    continue
                for ch in c_data.get("data", {}).get("items", []):
                    if ch.get("type") == 1:
                        cid = str(ch["id"])
                        text_channels.append(cid)
                        if gid:
                            channel_to_guild[cid] = gid
                        try:
                            m_data = await _kook_get_json(
                                http, "/message/list", params={"target_id": cid, "page_size": 20}, attempts=2, deadline=deadline
                            )
                            for m in m_data.get("data", {}).get("items", []):
                                if m.get("id"):
                                    seen_msg_ids.add(str(m["id"]))
                        except Exception as exc:
                            print(f"Channel history warning channel={cid}: {exc}", flush=True)
        except Exception as exc:
            print(f"Channel discovery warning: {exc}", flush=True)

        async def process_incoming(
            channel_id: str,
            msg_id: str,
            author_id: str,
            is_bot: bool,
            content: str,
            is_mentioned: bool,
            is_dm: bool = False,
            created_ms=None,
            guild_id: str = "",
        ):
            if not msg_id or msg_id in seen_msg_ids:
                return
            if len(seen_msg_ids) > 5000:
                seen_msg_ids.clear()
            seen_msg_ids.add(msg_id)
            if is_bot or str(author_id) == bot_id:
                return
            if created_ms and max_message_age_seconds > 0:
                try:
                    age = time.time() - float(created_ms) / 1000.0
                except (TypeError, ValueError):
                    age = 0.0
                if age > max_message_age_seconds:
                    print(f"Skip stale msg_id={msg_id} age={age:.0f}s", flush=True)
                    return
            resolved_guild = str(guild_id or channel_to_guild.get(channel_id) or "")
            if is_dm:
                scope_key = f"dm:{author_id}"
            elif resolved_guild:
                scope_key = f"guild:{resolved_guild}"
            else:
                scope_key = f"channel:{channel_id}"
            try:
                reply = await asyncio.to_thread(session.handle_message, channel_id, content, is_mentioned, scope_key)
            except Exception as exc:
                print(f"Handle message error msg_id={msg_id} channel={channel_id}: {type(exc).__name__}: {exc}", flush=True)
                return
            if not reply:
                return
            endpoint = f"{KOOK_API_BASE}/direct-message/create" if is_dm else f"{KOOK_API_BASE}/message/create"
            chunks = split_reply_chunks(reply)
            for index, chunk in enumerate(chunks, 1):
                payload = {"type": 9, "target_id": channel_id, "content": chunk}
                if index == 1:
                    payload["quote"] = msg_id
                for send_attempt in range(2):
                    try:
                        resp = await http.post(endpoint, json=payload)
                        try:
                            body = resp.json()
                        except Exception:
                            body = {}
                        code = body.get("code")
                        print(
                            f"Reply {index}/{len(chunks)} msg_id={msg_id} channel={channel_id} "
                            f"http={resp.status_code} code={code} msg={body.get('message', '')} len={len(chunk)}",
                            flush=True,
                        )
                        if getattr(resp, "status_code", 200) == 429 and send_attempt == 0:
                            await asyncio.sleep(min(5.0, RETRY_BASE_DELAY))
                            continue
                        break
                    except Exception as exc:
                        print(f"Reply failed {index}/{len(chunks)} msg_id={msg_id}: {exc}", flush=True)
                        if send_attempt == 0:
                            await asyncio.sleep(min(2.0, RETRY_BASE_DELAY))

        async def poll_channels():
            while time.time() < deadline:
                interval = 12.0 if ws_connected else 4.0
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(interval, remaining))
                for cid in list(text_channels):
                    if time.time() >= deadline:
                        break
                    try:
                        m_resp = await http.get(f"{KOOK_API_BASE}/message/list", params={"target_id": cid, "page_size": 10})
                        if getattr(m_resp, "status_code", 200) == 429:
                            await asyncio.sleep(min(15.0, max(RETRY_BASE_DELAY * 2, 5.0)))
                            break
                        items = m_resp.json().get("data", {}).get("items", [])
                        for m in items[-5:]:
                            mid = str(m.get("id") or "")
                            if not mid or mid in seen_msg_ids:
                                continue
                            if m.get("type") not in {1, 9, 10}:
                                seen_msg_ids.add(mid)
                                continue
                            author = m.get("author") or {}
                            content = str(m.get("content") or "")
                            mentions = [str(x) for x in (m.get("mention") or [])]
                            mention_roles = [str(x) for x in (m.get("mention_roles") or [])]
                            is_mentioned = (
                                bot_id in mentions
                                or bool(mention_roles)
                                or f"(met){bot_id}(met)" in content
                                or "(rol)" in content
                            )
                            await process_incoming(
                                channel_id=cid,
                                msg_id=mid,
                                author_id=str(author.get("id") or ""),
                                is_bot=bool(author.get("bot")),
                                content=content,
                                is_mentioned=is_mentioned,
                                is_dm=False,
                                created_ms=m.get("create_time"),
                            )
                        if len(text_channels) > 1:
                            await asyncio.sleep(0.2)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass

        async def refresh_snapshot_loop():
            while time.time() < deadline:
                remaining_ref = deadline - time.time()
                if remaining_ref <= 0:
                    break
                await asyncio.sleep(min(1800.0, remaining_ref))
                if time.time() >= deadline:
                    break
                try:
                    updated = await asyncio.to_thread(server.refresh_cloud_snapshot)
                    if updated:
                        print("Hot-reloaded newer cloud snapshot data/games.sqlite", flush=True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"Snapshot refresh warning: {exc}", flush=True)

        poll_task = asyncio.create_task(poll_channels())
        refresh_task = asyncio.create_task(refresh_snapshot_loop())
        reconnect_attempt = 0
        try:
            while time.time() < deadline:
                ws_connected = False
                try:
                    gw_data = await _kook_get_json(http, "/gateway/index", params={"compress": 0}, attempts=3, deadline=deadline)
                    gw_url = (gw_data.get("data") or {}).get("url") if gw_data.get("code") == 0 else None
                    if not gw_url:
                        raise RuntimeError(f"Invalid KOOK gateway response: code={gw_data.get('code')} msg={gw_data.get('message')}")
                    sn = 0
                    last_pong_at = time.time()
                    async with websockets.connect(gw_url, open_timeout=30, close_timeout=10, ping_interval=None) as ws:
                        ws_connected = True
                        reconnect_attempt = 0
                        print("KOOK WebSocket connected", flush=True)

                        async def heartbeat():
                            while time.time() < deadline:
                                remaining_hb = deadline - time.time()
                                if remaining_hb <= 0:
                                    break
                                await asyncio.sleep(min(25.0, remaining_hb))
                                if time.time() >= deadline:
                                    break
                                if time.time() - last_pong_at > 80.0:
                                    print("KOOK WebSocket pong timeout; forcing reconnect", flush=True)
                                    try:
                                        await ws.close()
                                    except Exception:
                                        pass
                                    break
                                try:
                                    await ws.send(json.dumps({"s": 2, "sn": sn}))
                                except asyncio.CancelledError:
                                    raise
                                except Exception as hb_exc:
                                    print(f"Heartbeat send stopped: {hb_exc}", flush=True)
                                    break

                        hb_task = asyncio.create_task(heartbeat())
                        try:
                            while time.time() < deadline:
                                remaining_ws = deadline - time.time()
                                if remaining_ws <= 0:
                                    break
                                try:
                                    raw = await asyncio.wait_for(ws.recv(), timeout=min(35.0, max(0.05, remaining_ws)))
                                except asyncio.TimeoutError:
                                    continue
                                try:
                                    packet = json.loads(raw)
                                except Exception:
                                    continue
                                sig = packet.get("s")
                                if sig == 1:
                                    hello_code = (packet.get("d") or {}).get("code", 0)
                                    if hello_code != 0:
                                        print(f"KOOK hello error code={hello_code}; reconnecting", flush=True)
                                        break
                                    last_pong_at = time.time()
                                elif sig == 3:
                                    last_pong_at = time.time()
                                elif sig == 5:
                                    print(f"KOOK requested reconnect (s=5): {packet.get('d')}", flush=True)
                                    break
                                elif sig == 0:
                                    sn = max(sn, int(packet.get("sn") or 0))
                                    d = packet.get("d") or {}
                                    if d.get("type") == 255 or d.get("channel_type") not in {"GROUP", "PERSON"}:
                                        continue
                                    extra_author = d.get("extra", {}).get("author", {}) or {}
                                    content = str(d.get("content") or "")
                                    author_id = str(d.get("author_id") or "")
                                    is_dm = d.get("channel_type") == "PERSON"
                                    # In KOOK PERSON (DM) events, target_id is the bot's own ID; reply must go to author_id.
                                    channel_id = author_id if is_dm else str(d.get("target_id") or "")
                                    mentions = [str(x) for x in (d.get("extra", {}).get("mention") or [])]
                                    mention_roles = [str(x) for x in (d.get("extra", {}).get("mention_roles") or [])]
                                    is_mentioned = (
                                        is_dm
                                        or bot_id in mentions
                                        or bool(mention_roles)
                                        or f"(met){bot_id}(met)" in content
                                        or "(rol)" in content
                                    )
                                    await process_incoming(
                                        channel_id=channel_id,
                                        msg_id=str(d.get("msg_id") or ""),
                                        author_id=author_id,
                                        is_bot=bool(extra_author.get("bot")),
                                        content=content,
                                        is_mentioned=is_mentioned,
                                        is_dm=is_dm,
                                        created_ms=d.get("msg_timestamp"),
                                        guild_id=str((d.get("extra") or {}).get("guild_id") or ""),
                                    )
                        finally:
                            ws_connected = False
                            hb_task.cancel()
                            await asyncio.gather(hb_task, return_exceptions=True)

                    # Clean break (e.g. s=5 reconnect): pause briefly before reconnecting
                    remaining_after = deadline - time.time()
                    if remaining_after > 0:
                        await asyncio.sleep(min(RETRY_BASE_DELAY, remaining_after))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    ws_connected = False
                    reconnect_attempt += 1
                    remaining_err = deadline - time.time()
                    if remaining_err <= 0:
                        break
                    delay = min(60.0, RETRY_BASE_DELAY * (2 ** min(reconnect_attempt - 1, 5)), remaining_err)
                    print(
                        f"KOOK WebSocket disconnected ({type(exc).__name__}: {exc}); reconnecting in {delay:.1f}s",
                        flush=True,
                    )
                    await asyncio.sleep(delay)
        finally:
            poll_task.cancel()
            refresh_task.cancel()
            await asyncio.gather(poll_task, refresh_task, return_exceptions=True)


def main() -> None:
    token = os.getenv("KOOK_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing KOOK_TOKEN environment variable.")
    max_seconds = int(os.getenv("KOOK_MAX_RUNTIME_SECONDS", "19800"))
    asyncio.run(run_kook_ws(token, max_runtime_seconds=max_seconds))


if __name__ == "__main__":
    main()
