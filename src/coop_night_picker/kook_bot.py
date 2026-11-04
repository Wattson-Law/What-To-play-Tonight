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
    m_players = re.search(r"(\d{1,2})\s*(?:个?人|p|players?)", low)
    if m_players:
        params["players"] = max(2, min(16, int(m_players.group(1))))
    elif "双人" in cleaned or "两人" in cleaned or "情侣" in cleaned:
        params["players"] = 2
    elif "三人" in cleaned:
        params["players"] = 3
    elif "四人" in cleaned or "开黑" in cleaned:
        params.setdefault("players", 4)

    # Horror
    if any(w in cleaned for w in ("不恐怖", "不要恐怖", "别恐怖", "胆小", "一点都不行", "非恐怖")):
        params["horror"] = "none"
    elif any(w in cleaned for w in ("微恐", "心理恐怖", "一点点恐怖")):
        params["horror"] = "mild"
    elif any(w in cleaned for w in ("恐怖", "吓人", "越吓人越好", "惊悚")):
        params["horror"] = "scary"
    elif any(w in cleaned for w in ("都行", "无所谓", "随便", "不限")):
        params.setdefault("horror", "any")

    # Budget
    if any(w in low for w in ("免费", "白嫖", "demo", "试玩", "0元")):
        params["budget"] = "free_or_demo"
    elif any(w in low for w in ("50", "五十", "便宜", "平价")):
        params["budget"] = "under_50"
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
            params["tag"] = canonical
            break

    # Duration
    if any(w in cleaned for w in ("短局", "半小时", "30分钟")):
        params["session_duration"] = "short"
    elif any(w in cleaned for w in ("一两个小时", "两小时", "一晚")):
        params["session_duration"] = "medium"
    elif any(w in cleaned for w in ("通宵", "不限时", "长期")):
        params["session_duration"] = "long"

    has_specific = any(k in params for k in ("horror", "budget", "tag")) or bool(m_players)
    if has_specific and (any(w in cleaned for w in ("推荐", "玩什么", "玩啥", "来个", "有什么")) or defaults is not None):
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
    base_url = os.getenv("LLM_BASE_URL", "https://models.inference.ai.azure.com").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
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

    def __init__(self, cooldown_sec: float = 1.5):
        self.pending_channels: dict[str, dict[str, Any]] = {}
        self.last_reply_at: dict[str, float] = {}
        self.cooldown_sec = cooldown_sec

    def handle_message(self, channel_id: str, text: str, is_mentioned: bool = True) -> str | None:
        now = time.time()
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
        if now - self.last_reply_at.get(channel_id, 0.0) < self.cooldown_sec:
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
            price_str = "免费" if card["is_free"] else f"¥{card['price_cny']}"
            return (
                f"### 🎮 《{card['name']}》（AppID: `{card['appid']}`）\n"
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
                lines.append(f"- **《{it.get('name')}》**（AppID: `{it.get('appid')}`）— {it.get('review_tier_zh', '未知')}")
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
            self.last_reply_at[channel_id] = now
            p = intent["params"]
            res = server.recommend_coop_games(
                horror=p.get("horror", "any"),
                budget=p.get("budget", "any"),
                players=int(p.get("players") or 4),
                mode=p.get("mode", "online"),
                tag=p.get("tag"),
                confirmed=True,
                limit_per_tier=3,
            )
            if res.get("status") != "ok" or not res.get("markdown_preview"):
                return "当前筛选条件下暂无匹配游戏，试试放宽条件（例如：`4人 都行 价格无所谓`）！"
            return res["markdown_preview"]

        self.pending_channels[channel_id] = intent.get("params") or {}
        self.last_reply_at[channel_id] = now
        return (
            "🎮 **今晚玩什么 · 开黑选游助手**\n"
            "请直接回复一句你的条件（不用再次@我），例如：`4人 不恐怖 免费 独立` 或 `2人 微恐 50以内`：\n"
            "1. **人数**：2人 / 3-4人 / 5人以上\n"
            "2. **恐怖接受度**：不恐怖 / 微恐 / 越吓人越好 / 都行\n"
            "3. **预算**：免费或Demo / 50元以内 / 价格不限\n"
            "4. **偏好标签（可选）**：独立 / Demo试玩 / 生存 / 解谜 / 肉鸽 / 沙盒 / 欢乐"
        )


async def run_kook_ws(token: str, max_runtime_seconds: int = 19800) -> None:
    """Connect to KOOK WebSocket gateway AND poll text channels so no message is ever missed."""
    import websockets

    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as http:
        me_resp = await http.get(f"{KOOK_API_BASE}/user/me")
        me_data = me_resp.json()
        if me_data.get("code") != 0:
            raise RuntimeError(f"KOOK auth failed: code={me_data.get('code')} msg={me_data.get('message')}")
        bot_id = str(me_data["data"]["id"])
        bot_name = me_data["data"].get("username", "Bot")
        print(f"KOOK bot authenticated: {bot_name} (id={bot_id})", flush=True)

        session = KookBotSession()
        seen_msg_ids: set[str] = set()
        started_at = time.time()

        # Discover text channels and snapshot existing message IDs on startup
        text_channels: list[str] = []
        try:
            g_resp = await http.get(f"{KOOK_API_BASE}/guild/list")
            for g in g_resp.json().get("data", {}).get("items", []):
                c_resp = await http.get(f"{KOOK_API_BASE}/channel/list", params={"guild_id": g["id"]})
                for ch in c_resp.json().get("data", {}).get("items", []):
                    if ch.get("type") == 1:
                        cid = str(ch["id"])
                        text_channels.append(cid)
                        # Pre-warm pending state on default channels so direct follow-up replies work immediately
                        session.pending_channels.setdefault(cid, {})
                        m_resp = await http.get(f"{KOOK_API_BASE}/message/list", params={"target_id": cid, "page_size": 20})
                        for m in m_resp.json().get("data", {}).get("items", []):
                            if m.get("id"):
                                seen_msg_ids.add(str(m["id"]))
        except Exception as exc:
            print(f"Channel discovery warning: {exc}", flush=True)

        async def process_incoming(channel_id: str, msg_id: str, author_id: str, is_bot: bool, content: str, is_mentioned: bool, is_dm: bool = False):
            if not msg_id or msg_id in seen_msg_ids:
                return
            seen_msg_ids.add(msg_id)
            if is_bot or str(author_id) == bot_id:
                return
            reply = session.handle_message(channel_id, content, is_mentioned=is_mentioned)
            if reply:
                endpoint = f"{KOOK_API_BASE}/direct-message/create" if is_dm else f"{KOOK_API_BASE}/message/create"
                payload = {"type": 9, "target_id": channel_id, "content": reply, "quote": msg_id}
                await http.post(endpoint, json=payload)
                print(f"Replied to msg_id={msg_id} in channel={channel_id}", flush=True)

        async def poll_channels():
            while time.time() - started_at < max_runtime_seconds:
                await asyncio.sleep(2.5)
                for cid in list(text_channels):
                    try:
                        m_resp = await http.get(f"{KOOK_API_BASE}/message/list", params={"target_id": cid, "page_size": 10})
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
                            )
                    except Exception:
                        pass

        poll_task = asyncio.create_task(poll_channels())
        try:
            gw_resp = await http.get(f"{KOOK_API_BASE}/gateway/index", params={"compress": 0})
            gw_url = gw_resp.json()["data"]["url"]
            sn = 0
            async with websockets.connect(gw_url, ping_interval=None) as ws:
                async def heartbeat():
                    while time.time() - started_at < max_runtime_seconds:
                        await asyncio.sleep(26)
                        await ws.send(json.dumps({"s": 2, "sn": sn}))

                hb_task = asyncio.create_task(heartbeat())
                try:
                    while time.time() - started_at < max_runtime_seconds:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=35.0)
                        except asyncio.TimeoutError:
                            continue
                        packet = json.loads(raw)
                        if packet.get("s") == 0:
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
                            )
                finally:
                    hb_task.cancel()
        finally:
            poll_task.cancel()


def main() -> None:
    token = os.getenv("KOOK_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing KOOK_TOKEN environment variable.")
    max_seconds = int(os.getenv("KOOK_MAX_RUNTIME_SECONDS", "19800"))
    asyncio.run(run_kook_ws(token, max_runtime_seconds=max_seconds))


if __name__ == "__main__":
    main()
