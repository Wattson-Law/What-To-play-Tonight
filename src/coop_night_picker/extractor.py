from __future__ import annotations

import html
import json
import math
import re
from collections import Counter

from .constants import CATEGORY_ZH, REVIEW_KEYWORDS, TAG_ZH

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

_CN_NUM = {"双": 2, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "八": 8, "十": 10}


def infer_max_players(text: str | None, categories: list[str] | None = None, tags: list[str] | None = None) -> int | None:
    blob = " ".join(filter(None, [text or "", " ".join(categories or []), " ".join(tags or [])]))
    if not blob:
        return None
    for pat in (
        r"(?:1|2|3|4)\s*[-~至到]\s*(\d{1,2})\s*(?:人|名玩家|players?)",
        r"(?:最多|至多|上限|支持|up\s+to)\s*(\d{1,2})\s*(?:人|名玩家|players?)",
        r"(\d{1,2})\s*(?:人|players?)\s*(?:在线|本地|同屏|联机|合作|开黑|co-?op)",
    ):
        m = re.search(pat, blob, re.I)
        if m:
            n = int(m.group(1))
            if 2 <= n <= 64:
                return n
    m_cn = re.search(r"([双两二三四五六八十])人(?:在线|本地|同屏|联机|合作|开黑|成行|同行|解谜|生存)?", blob)
    if m_cn and m_cn.group(1) in _CN_NUM:
        return _CN_NUM[m_cn.group(1)]
    return None


def parse_search_html(raw):
    if isinstance(raw, dict):
        raw = raw.get("html") or raw.get("text") or ""
    if BeautifulSoup is None:
        return []
    s = BeautifulSoup(raw, "html.parser")
    out = []
    for a in s.select("a.search_result_row"):
        try:
            aid = int(a.get("data-ds-appid", "").split(",")[0])
        except Exception:
            continue
        out.append(
            {
                "appid": aid,
                "name": a.select_one(".title").get_text(" ", strip=True) if a.select_one(".title") else "",
                "tag_ids": [int(x) for x in re.findall(r"\d+", a.get("data-ds-tagids", ""))],
            }
        )
    return out


def parse_curator_recommendations(payload_or_html, curator_name: str = "Steam鉴赏家", clanid: int = 0) -> list[dict]:
    raw_html = (
        payload_or_html.get("results_html") or payload_or_html.get("html") or payload_or_html.get("text") or ""
        if isinstance(payload_or_html, dict)
        else str(payload_or_html or "")
    )
    if BeautifulSoup is None or not raw_html:
        return []
    soup = BeautifulSoup(raw_html, "html.parser")
    out = []
    for rec in soup.select("div.recommendation"):
        cap = rec.select_one("a[data-ds-appid]")
        if not cap:
            continue
        try:
            appid = int(str(cap.get("data-ds-appid", "")).split(",")[0])
        except Exception:
            continue
        tag_ids = [int(x) for x in re.findall(r"\d+", str(cap.get("data-ds-tagids", "")))]
        img = cap.select_one("img")
        name = (img.get("alt", "").strip() if img else "") or f"App {appid}"
        header_image = img.get("src", "").strip() if img else ""
        disc_block = cap.select_one(".discount_block")
        price_final = 0.0
        discount_pct = 0
        if disc_block:
            try:
                price_final = float(disc_block.get("data-price-final", 0) or 0) / 100.0
            except Exception:
                price_final = 0.0
            try:
                discount_pct = int(disc_block.get("data-discount", 0) or 0)
            except Exception:
                discount_pct = 0

        if rec.select_one(".color_not_recommended"):
            attitude = "not_recommended"
            voted_up = 0
        elif rec.select_one(".color_informational"):
            attitude = "informational"
            voted_up = 1
        else:
            attitude = "recommended"
            voted_up = 1

        desc_el = rec.select_one(".recommendation_desc")
        blurb = re.sub(r"\s+", " ", desc_el.get_text(" ", strip=True)) if desc_el else ""
        link_el = rec.select_one("a.recommendation_link") or cap
        review_url = (link_el.get("href", "") if link_el else "") or (
            f"https://store.steampowered.com/app/{appid}/?curator_clanid={clanid}" if clanid else f"https://store.steampowered.com/app/{appid}/"
        )
        quote_text = f"[鉴赏家·{curator_name}] {blurb}".strip() if blurb else f"[鉴赏家·{curator_name}] 列入精选推荐"
        dims = [k for k, pat in REVIEW_KEYWORDS.items() if re.search(pat, blurb, re.I)]
        if not dims:
            dims = ["coop_quality"] if voted_up else ["dealbreaker"]
        tag_names = [TAG_ZH[t] for t in tag_ids if t in TAG_ZH]
        is_demo = "demo" in name.lower() or "试玩" in name or "试玩" in blurb
        max_players = infer_max_players(blurb, tags=tag_names)
        out.append(
            {
                "appid": appid,
                "name": name,
                "tag_ids": tag_ids,
                "tags_json": tag_names,
                "price_cny": price_final,
                "is_free": int(price_final == 0.0 and disc_block is not None),
                "has_demo": int(is_demo),
                "discount_pct": discount_pct,
                "header_image": header_image,
                "curator_name": curator_name,
                "clanid": int(clanid or 0),
                "attitude": attitude,
                "voted_up": voted_up,
                "blurb": blurb,
                "quote_text": quote_text,
                "dimensions_json": dims,
                "review_url": review_url,
                "max_players": max_players,
            }
        )
    return out


def parse_appdetails(payload, appid=None):
    d = payload.get(str(appid), payload) if isinstance(payload, dict) else {}
    d = d.get("data", d) if isinstance(d, dict) else {}
    p = d.get("price_overview") or {}
    cats = d.get("categories") or []
    category_ids = [int(x["id"]) for x in cats if isinstance(x, dict) and x.get("id") is not None]
    category_names = [CATEGORY_ZH.get(int(x["id"]), x.get("description", "")) if isinstance(x, dict) else str(x) for x in cats]
    genres = d.get("genres") or []
    genre_names = [str(g.get("description", "")).strip() for g in genres if isinstance(g, dict) and g.get("description")]
    app_type = d.get("type", "game")
    demos = d.get("demos") or []
    short_desc = d.get("short_description")
    max_players = infer_max_players(short_desc, categories=category_names, tags=genre_names)
    return {
        "appid": appid or d.get("steam_appid"),
        "name": d.get("name", ""),
        "type": app_type,
        "is_free": int(d.get("is_free", False)),
        "price_cny": p.get("final", 0) / 100,
        "discount_pct": p.get("discount_percent", 0),
        "categories_json": category_names,
        "category_ids": category_ids,
        "genre_names": genre_names,
        "short_desc": short_desc,
        "header_image": d.get("header_image"),
        "has_demo": int(app_type == "demo" or bool(demos)),
        "demo_appid": (demos or [{}])[0].get("appid"),
        "fullgame_appid": (d.get("fullgame") or {}).get("appid"),
        "max_players": max_players,
    }


def parse_store_page(raw):
    text = raw if isinstance(raw, str) else raw.get("text", raw.get("__text__", ""))
    tags = []
    m = re.search(r"InitAppTagModal\s*\([^,]+,\s*(\[.*?\])\s*\)", text, re.S)
    if m:
        try:
            vals = json.loads(html.unescape(m.group(1)))
            tags = [{"id": int(x.get("tagid", x.get("id", 0))), "name": x.get("name", "")} for x in vals if isinstance(x, dict)]
        except Exception:
            pass
    props = []
    for node in re.findall(r"<[^>]*data-featuretarget=[\"']appreviews[\"'][^>]*>", text, re.I | re.S):
        m = re.search(r"data-props=[\"'](.*?)[\"']", node, re.I | re.S)
        if m:
            try:
                props.append(json.loads(html.unescape(m.group(1))))
            except Exception:
                pass
    return {"tags": tags, "review_props": props}


def parse_appreviews(payload):
    if not isinstance(payload, dict):
        return {"reviews": [], "summary": {}}
    return {
        "reviews": payload.get("reviews", []),
        "summary": payload.get("query_summary", {}),
        "metadata": {k: v for k, v in payload.items() if k not in {"reviews", "query_summary"}},
    }


def extract_quotes(raw_review, voted_up=True, max_quotes=3, appid=None):
    text = raw_review.get("review", "") if isinstance(raw_review, dict) else str(raw_review)
    votes = int(raw_review.get("votes_up", 0)) if isinstance(raw_review, dict) else 0
    scored = []
    for s in re.split(r"[。！？!？\n]+", text):
        s = s.strip()
        if 6 <= len(s) <= 80:
            ds = [k for k, p in REVIEW_KEYWORDS.items() if re.search(p, s, re.I)]
            if ds:
                scored.append((len(ds) * math.log(votes + 2), s, ds))
    scored.sort(reverse=True)
    rid = raw_review.get("recommendationid", "") if isinstance(raw_review, dict) else ""
    author = (raw_review.get("author") or {}).get("steamid", "") if isinstance(raw_review, dict) else ""
    appid = appid or (raw_review.get("appid", "") if isinstance(raw_review, dict) else "")
    url = raw_review.get("review_url") if isinstance(raw_review, dict) else None
    if not url and author and appid:
        url = f"https://steamcommunity.com/profiles/{author}/recommended/{appid}/"
    out = []
    for _, s, ds in scored[:max_quotes]:
        assert s in text
        out.append(
            {
                "quote_text": s,
                "dimensions_json": ds,
                "voted_up": int(voted_up),
                "votes_helpful": votes,
                "recommendation_id": rid,
                "playtime_hours": round((raw_review.get("author") or {}).get("playtime_at_review", 0) / 60, 2)
                if isinstance(raw_review, dict)
                else 0.0,
                "review_url": url or "https://steamcommunity.com/",
            }
        )
    return out


def build_avoid_reasons(reviews):
    c = Counter()
    for r in reviews:
        ds = {d for q in extract_quotes(r, False, 99) for d in q["dimensions_json"]}
        for d in ds:
            if d in {"network", "dealbreaker"}:
                c[d] += 1
    return [f"{k}({v}条提及)" for k, v in c.items()]


def derive_review_summary(props):
    if isinstance(props, list):
        props = props[0] if props else {}
    if not isinstance(props, dict):
        return {}
    so = props.get("summary_options", {}) or {}

    def norm(x):
        if not isinstance(x, dict):
            return {}
        total = x.get("nReviews", x.get("total_reviews", x.get("cReviews", 0)))
        pos = x.get("nReviewsPositive", x.get("nRecommended", x.get("positive_reviews", 0)))
        pct = x.get("fRating", x.get("positive_pct"))
        if pct is None and total:
            pct = 100 * pos / total
        return {"total": total, "positive": pos, "pct": pct or 0, "score": x.get("eReviewScore", x.get("review_score"))}

    out = {
        "global": norm(so.get("summaryGlobalNoOutliers") or so.get("summaryDisplayed") or props.get("summaryGlobalNoOutliers")),
        "schinese": norm(so.get("summaryYourLanguage")),
    }
    for x in (props.get("filter_options", {}) or {}).get("rgLanguageOutliers", []):
        if str(x.get("strLanguage", "")).lower() in {"schinese", "chinese"}:
            out["schinese"] = norm(x)
    return out
