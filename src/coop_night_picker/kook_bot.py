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


def parse_intent_from_text(text: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministically parse KOOK user message into tool intent & parameters."""
    cleaned = re.sub(r"\((?:met|rol)\)\d+\((?:met|rol)\)", " ", text or "").strip()
    cleaned = re.sub(r"@\S+", " ", cleaned).strip()
    low = cleaned.lower()

    m_card = re.search(r"(?:详情|卡片|card)\s*(\d{3,10})", low)
    if m_card:
        return {"action": "card", "appid": int(m_card.group(1))}

    m_log = re.search(r"(玩过|已玩|避雷|拉黑|想玩)\s*(\d{3,10})", cleaned)
    if m_log:
        word, aid = m_log.group(1), int(m_log.group(2))
        status = "played" if word in {"玩过", "已玩"} else ("avoided" if word in {"避雷", "拉黑"} else "wishlist")
        return {"action": "log", "appid": aid, "status": status}

    m_search = re.search(r"^(?:搜|搜索|查|查找)\s+(.+)$", cleaned)
    if m_search:
        return {"action": "search", "query": m_search.group(1).strip()}

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
    elif "四人" in cleaned or "开黑" in cleaned:
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

    core_filled = sum(1 for key in ("players", "horror", "budget") if params.get(key) not in (None, ""))
    has_trigger = any(w in cleaned for w in ("推荐", "玩什么", "玩啥", "来个", "有什么"))
    enough = core_filled >= 2 and (has_trigger or m_players or defaults is not None or core_filled >= 3)
    if enough or (has_trigger and params.get("tag")):
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
    """Stateful KOOK channel coordinator with rate limiting."""

    def __init__(self, cooldown_sec: float = 1.5, pending_ttl_sec: float = PENDING_TTL_SECONDS):
        self.pending_channels: dict[str, dict[str, Any]] = {}
        self.pending_since: dict[str, float] = {}
        self.last_reply_at: dict[str, float] = {}
        self.cooldown_sec = cooldown_sec
        self.pending_ttl_sec = pending_ttl_sec

    def handle_message(self, channel_id: str, text: str, is_mentioned: bool = True) -> str | None:
        now = time.time()
        if channel_id in self.pending_channels:
            since = self.pending_since.get(channel_id, now)
            if now - since > self.pending_ttl_sec:
                self.pending_channels.pop(channel_id, None)
                self.pending_since.pop(channel_id, None)
        in_pending = channel_id in self.pending_channels
        stripped = (text or "").strip()
        if re.search(r"\((?:met|rol)\)\d+\((?:met|rol)\)", stripped):
            is_mentioned = True
        trigger_words = (
            "今晚玩什么",
            "今晚玩啥",
            "玩什么",
            "玩啥",
            "推荐",
            "有什么好玩",
            "联机游戏",
            "说话",
            "在吗",
            "/start",
            "/help",
            "搜 ",
            "详情",
            "玩过",
            "避雷",
        )
        if not is_mentioned and not in_pending and not any(w in stripped for w in trigger_words):
            return None
        # Cooldown only guards unsolicited spam; while a dialog is open the user is answering us.
        if not in_pending and now - self.last_reply_at.get(channel_id, 0.0) < self.cooldown_sec:
            return None

        prior = self.pending_channels.get(channel_id)
        intent = parse_intent_from_text(stripped, defaults=prior)

        if intent["action"] == "card":
            self.last_reply_at[channel_id] = now
            card = server.get_game_card(intent["appid"])
            if card.get("status") != "ok":
                return f"未在库中找到 AppID `{intent['appid']}`，可以用 `搜 游戏名` 先查一下。"
            quotes = card.get("positive_quotes") or []
            q_line = f"\n> {quotes[0]['quote_text']}" if quotes else ""
            if card["is_free"]:
                price_str = "免费"
            elif card.get("price_cny"):
                price_str = f"¥{float(card['price_cny']):g}"
            elif card.get("has_demo"):
                price_str = "免费试玩 Demo"
            else:
                price_str = "待定"
            return (
                f"**🎮 《{card['name']}》**（AppID: `{card['appid']}`）\n"
                f"- **评价**：{card['stars']} {card['review_tier']}（全语言 {card['positive_pct']}%｜简中 {card.get('schinese_pct') or '—'}%）\n"
                f"- **价格**：{price_str}｜**支持人数**：{card.get('max_players') or '?'}人\n"
                f"- **鉴赏家背书**：{card.get('curator_count', 0)} 位（{'、'.join(card.get('curators') or []) or '无'}）"
                f"{q_line}"
            )

        if intent["action"] == "search":
            self.last_reply_at[channel_id] = now
            res = server.resolve_game(intent["query"], limit=5)
            items = res.get("items") or []
            if not items:
                return f"没有搜到与「{intent['query']}」相关的联机游戏。"
            lines = [f"🔎 **「{intent['query']}」搜索结果**："]
            for it in items[:5]:
                lines.append(f"- **《{it.get('name')}》**（AppID: `{it.get('appid')}`）— {it.get('review_tier_zh', 'Steam 商店')}")
            lines.append("\n发送 `@我 详情 <AppID>` 可查看完整评测卡，发送 `@我 玩过 <AppID>` 可排除该游戏。")
            return "\n".join(lines)

        if intent["action"] == "log":
            self.last_reply_at[channel_id] = now
            server.log_play(intent["appid"], intent["status"])
            label = {"played": "已玩过（后续自动排除）", "avoided": "已避雷（后续自动排除）", "wishlist": "想玩清单"}[intent["status"]]
            return f"✅ 已记录 AppID `{intent['appid']}` 为 **{label}**！"

        if intent["action"] == "ask":
            llm_guess = call_llm_for_params(stripped)
            if isinstance(llm_guess, dict) and any(llm_guess.get(k) for k in ("horror", "budget", "tag", "players")):
                p = intent["params"]
                for k in ("horror", "budget", "players", "mode", "tag"):
                    if llm_guess.get(k) not in (None, "null", ""):
                        p[k] = llm_guess[k]
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
            res = server.recommend_coop_games(
                horror=p.get("horror", "any"),
                budget=p.get("budget", "any"),
                players=_coerce_players(p.get("players"), 4),
                mode=p.get("mode", "online"),
                tag=p.get("tag"),
                confirmed=True,
                limit_per_tier=3,
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

        session = KookBotSession()
        seen_msg_ids: set[str] = set()
        max_message_age_seconds = int(os.getenv("KOOK_MAX_MESSAGE_AGE_SECONDS", "900"))
        ws_connected = False

        # Discover text channels and snapshot existing message IDs on startup (without pre-warming pending dialogs)
        text_channels: list[str] = []
        try:
            g_data = await _kook_get_json(http, "/guild/list", attempts=2, deadline=deadline)
            for g in g_data.get("data", {}).get("items", []):
                try:
                    c_data = await _kook_get_json(http, "/channel/list", params={"guild_id": g["id"]}, attempts=2, deadline=deadline)
                except Exception as exc:
                    print(f"Channel list warning guild={g.get('id')}: {exc}", flush=True)
                    continue
                for ch in c_data.get("data", {}).get("items", []):
                    if ch.get("type") == 1:
                        cid = str(ch["id"])
                        text_channels.append(cid)
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
        ):
            if not msg_id or msg_id in seen_msg_ids:
                return
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
            try:
                reply = await asyncio.to_thread(session.handle_message, channel_id, content, is_mentioned)
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

        poll_task = asyncio.create_task(poll_channels())
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
                                    channel_id = str(d.get("target_id") or "")
                                    mentions = [str(x) for x in (d.get("extra", {}).get("mention") or [])]
                                    mention_roles = [str(x) for x in (d.get("extra", {}).get("mention_roles") or [])]
                                    is_dm = d.get("channel_type") == "PERSON"
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
                                        author_id=str(d.get("author_id") or ""),
                                        is_bot=bool(extra_author.get("bot")),
                                        content=content,
                                        is_mentioned=is_mentioned,
                                        is_dm=is_dm,
                                        created_ms=d.get("msg_timestamp"),
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
            await asyncio.gather(poll_task, return_exceptions=True)


def main() -> None:
    token = os.getenv("KOOK_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing KOOK_TOKEN environment variable.")
    max_seconds = int(os.getenv("KOOK_MAX_RUNTIME_SECONDS", "19800"))
    asyncio.run(run_kook_ws(token, max_runtime_seconds=max_seconds))


if __name__ == "__main__":
    main()
