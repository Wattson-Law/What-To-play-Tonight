from __future__ import annotations

import datetime
import html
import json
import math
import re
from collections import Counter

from .constants import CATEGORY_ZH, INDIE_PUBLISHER_WHITELIST, NEGATIVE_CURATOR_PATTERNS, REVIEW_KEYWORDS, TAG_ZH

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

_CN_NUM = {"双": 2, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "八": 8, "十": 10}
_EN_NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ten": 10, "twelve": 12, "sixteen": 16}
_NEGATED_PRAISE_RE = re.compile(
    r"(?:不会|绝不|毫不|并不|没让|从未|不至于|无需|没有|并非|绝非|不算)"
    r"(?:让你|让人|令我|令人|显得)?(?:失望|敷衍|半成品|粗制滥造|差评|坐牢|赶工|骗钱|不耐玩)"
)


def _parse_num_token(tok: str) -> int | None:
    t = str(tok or "").strip().lower()
    if t.isdigit():
        return int(t)
    if t in _EN_NUM:
        return _EN_NUM[t]
    if t in _CN_NUM:
        return _CN_NUM[t]
    return None


def _strip_html(raw: str | None) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(html.unescape(cleaned).split())


def infer_max_players(text: str | None, categories: list[str] | None = None, tags: list[str] | None = None) -> int | None:
    blob = " ".join(filter(None, [text or "", " ".join(categories or []), " ".join(tags or [])]))
    if not blob:
        return None
    # Strip perspective phrases like "第一人称 / 第三人称" so "三人" isn't misread as 3 players.
    cleaned_blob = re.sub(r"第[一二三四]人称", " ", blob)
    for pat in (
        r"(?:1|2|3|4)\s*(?:[-~–至到]|to)\s*(\d{1,2})\s*(?:人|名玩家|位玩家|players?|friends?)",
        r"(?:最多可容纳|最多|至多|上限|支持|up\s+to|for\s+up\s+to|with\s+up\s+to)\s*(\d{1,2}|two|three|four|five|six|seven|eight|ten|twelve|sixteen)\s*(?:人|名玩家|位玩家|players?|friends?|people)",
        r"\b(\d{1,2}|two|three|four|five|six|eight)\s*[-–]\s*players?\b",
        r"(\d{1,2})\s*(?:人|名玩家|players?)\s*(?:在线|本地|同屏|联机|合作|开黑|co-?op)",
    ):
        m = re.search(pat, cleaned_blob, re.I)
        if m:
            n = _parse_num_token(m.group(1))
            if n is not None and 2 <= n <= 64:
                return n
    m_cn = re.search(
        r"(?<!第)([双两二三四五六八十])个?人(?!称|类|物|生|工|员|士|民|口|家|情|格|性|设|像|偶|形|体|头|脸|眼|手|脚|们)"
        r"(?:在线|本地|同屏|联机|合作|开黑|成行|同行|解谜|生存|游玩|组队|派对|小队)?",
        cleaned_blob,
    )
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
        rel_el = a.select_one(".search_released")
        out.append(
            {
                "appid": aid,
                "name": a.select_one(".title").get_text(" ", strip=True) if a.select_one(".title") else "",
                "tag_ids": [int(x) for x in re.findall(r"\d+", a.get("data-ds-tagids", ""))],
                "release_date_text": rel_el.get_text(" ", strip=True) if rel_el else "",
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
        price_final: float | None = None
        discount_pct = 0
        if disc_block:
            try:
                price_final = float(disc_block.get("data-price-final", 0) or 0) / 100.0
            except Exception:
                price_final = None
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
        # Strip curator self-promo noise (activation-code giveaways, follow-me lines) from the blurb.
        blurb = re.sub(r"每日福利游戏激活码[：:]\s*[A-Za-z0-9\-]+", "", blurb or "")
        blurb = re.sub(r"(求关注|点个关注|欢迎关注|记得关注)[~～!！。\s]*", "", blurb)
        blurb = re.sub(r"^[\s、，,。:：\-]+|[\s、，,。:：\-]+$", "", blurb).strip()
        blurb_for_neg = _NEGATED_PRAISE_RE.sub("", blurb) if blurb else ""
        if blurb_for_neg and re.search(NEGATIVE_CURATOR_PATTERNS, blurb_for_neg, re.I):
            attitude = "not_recommended"
            voted_up = 0
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
    raw_short_desc = d.get("short_description")
    short_desc = html.unescape(raw_short_desc).strip() if isinstance(raw_short_desc, str) else raw_short_desc
    is_free = bool(d.get("is_free", False))
    price_cny = (p["final"] / 100.0) if (isinstance(p, dict) and p.get("final") is not None) else (0.0 if is_free else None)
    rel_obj = d.get("release_date") or {}
    coming_soon = int(bool(rel_obj.get("coming_soon")))
    release_date = str(rel_obj.get("date") or "").strip()
    langs = str(d.get("supported_languages") or "")
    has_schinese = int("简体中文" in langs or "simplified chinese" in langs.lower())
    has_trailer = int(bool(d.get("movies")))
    screenshot_count = len(d.get("screenshots") or [])
    developers_json = [str(x).strip() for x in (d.get("developers") or []) if str(x).strip()]
    publishers_json = [str(x).strip() for x in (d.get("publishers") or []) if str(x).strip()]
    raw_fullgame = (d.get("fullgame") or {}).get("appid")
    try:
        fullgame_appid = int(raw_fullgame) if raw_fullgame is not None and str(raw_fullgame).strip().isdigit() else None
    except (TypeError, ValueError):
        fullgame_appid = None
    raw_demo_id = (demos or [{}])[0].get("appid")
    try:
        demo_appid = int(raw_demo_id) if raw_demo_id is not None and str(raw_demo_id).strip().isdigit() else None
    except (TypeError, ValueError):
        demo_appid = None
    max_players = infer_max_players(short_desc, categories=category_names, tags=genre_names)
    if max_players is None:
        long_desc = _strip_html(d.get("about_the_game") or d.get("detailed_description"))[:2500]
        if long_desc:
            max_players = infer_max_players(long_desc, categories=category_names, tags=genre_names)
    return {
        "appid": appid or d.get("steam_appid"),
        "name": html.unescape(str(d.get("name", ""))).strip(),
        "type": app_type,
        "is_free": int(is_free),
        "coming_soon": coming_soon,
        "release_date": release_date,
        "has_schinese": has_schinese,
        "has_trailer": has_trailer,
        "screenshot_count": screenshot_count,
        "developers_json": developers_json,
        "publishers_json": publishers_json,
        "price_cny": price_cny,
        "discount_pct": p.get("discount_percent", 0) if isinstance(p, dict) else 0,
        "categories_json": category_names,
        "category_ids": category_ids,
        "genre_names": genre_names,
        "short_desc": short_desc,
        "header_image": d.get("header_image"),
        "has_demo": int(app_type == "demo" or bool(demos)),
        "demo_appid": demo_appid,
        "fullgame_appid": fullgame_appid,
        "max_players": max_players,
    }


def parse_store_page(raw):
    text = raw if isinstance(raw, str) else raw.get("text", raw.get("__text__", ""))
    tags = []
    m = re.search(r"InitAppTagModal\s*\([^,]+,\s*", text, re.S)
    if m:
        rest = text[m.end() :].lstrip()
        if rest.startswith("["):
            vals = None
            for candidate in (rest, html.unescape(rest)):
                try:
                    vals, _ = json.JSONDecoder().raw_decode(candidate)
                    break
                except Exception:
                    continue
            if isinstance(vals, list):
                tags = [
                    {"id": int(x.get("tagid", x.get("id", 0))), "name": x.get("name", "")}
                    for x in vals
                    if isinstance(x, dict)
                ]
    props = []
    if BeautifulSoup is not None and text:
        soup = BeautifulSoup(text, "html.parser")
        for el in soup.find_all(attrs={"data-featuretarget": re.compile(r"^appreviews$", re.I)}):
            raw_props = el.get("data-props")
            if not raw_props:
                continue
            for candidate in (raw_props, html.unescape(raw_props)):
                try:
                    props.append(json.loads(candidate))
                    break
                except Exception:
                    continue
    if not props and text:
        for node in re.findall(r"<[^>]*data-featuretarget=[\"']appreviews[\"'][^>]*>", text, re.I | re.S):
            m = re.search(r"""data-props=(["'])(.*?)\1""", node, re.I | re.S)
            if m:
                try:
                    props.append(json.loads(html.unescape(m.group(2))))
                except Exception:
                    pass
    low = text.lower() if text else ""
    has_ai_disclosure = int("ai generated content disclosure" in low or "ai 生成内容披露" in low or "ai生成内容披露" in low)
    return {"tags": tags, "review_props": props, "has_ai_disclosure": has_ai_disclosure}


_MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def parse_release_age_days(release_date_str: str | dict | None, today: datetime.date | None = None) -> int | None:
    """Parse Steam CN/EN release date string into integer days since release, or None if unreleased/unknown."""
    if isinstance(release_date_str, dict):
        release_date_str = release_date_str.get("date")
    raw = str(release_date_str or "").strip()
    if not raw:
        return None
    ref = today or datetime.datetime.now(datetime.timezone.utc).date()
    parsed: datetime.date | None = None

    m_cn = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", raw)
    if m_cn:
        try:
            parsed = datetime.date(int(m_cn.group(1)), int(m_cn.group(2)), int(m_cn.group(3)))
        except ValueError:
            parsed = None

    if parsed is None:
        m_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
        if m_iso:
            try:
                parsed = datetime.date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            except ValueError:
                parsed = None

    if parsed is None:
        m_en1 = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", raw)
        m_en2 = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9}),?\s+(\d{4})", raw)
        if m_en1:
            mon = _MONTH_MAP.get(m_en1.group(1)[:3].lower())
            if mon:
                try:
                    parsed = datetime.date(int(m_en1.group(3)), mon, int(m_en1.group(2)))
                except ValueError:
                    parsed = None
        elif m_en2:
            mon = _MONTH_MAP.get(m_en2.group(2)[:3].lower())
            if mon:
                try:
                    parsed = datetime.date(int(m_en2.group(3)), mon, int(m_en2.group(1)))
                except ValueError:
                    parsed = None

    if parsed is None:
        return None
    delta = (ref - parsed).days
    return max(0, delta) if delta >= -1 else None


def compute_potential_score(
    *,
    current_players: int = 0,
    followers: int = 0,
    age_days: int | None = None,
    recent_news_count: int = 0,
    has_schinese: int | bool = 0,
    has_trailer: int | bool = 0,
    has_ai_disclosure: int | bool = 0,
    total_reviews: int = 0,
    positive_pct: float = 0.0,
    developers: list[str] | None = None,
    publishers: list[str] | None = None,
    from_wishlist: bool = False,
    delta_followers: int = 0,
    delta_ccu: int = 0,
) -> float:
    """Score early-stage Demo potential using live traction, follower proxy, 24h velocity, polish, and freshness."""
    ccu = max(0, int(current_players or 0))
    fol = max(0, int(followers or 0))
    news = max(0, int(recent_news_count or 0))
    revs = max(0, int(total_reviews or 0))
    pos = float(positive_pct or 0.0)
    d_fol = max(0, int(delta_followers or 0))
    d_ccu = max(0, int(delta_ccu or 0))

    has_traction = ccu >= 3 or fol >= 50 or news >= 1 or d_fol >= 10 or d_ccu >= 5 or (revs >= 5 and pos >= 80.0) or from_wishlist
    if not has_traction and not has_trailer:
        # Zero-traction, no-trailer, no-news fresh Demo -> noise floor
        base_noise = 1.0 if has_schinese else 0.5
        if has_ai_disclosure:
            base_noise = max(0.0, base_noise - 1.0)
        return round(min(2.0, base_noise + 0.2 * math.log1p(fol)), 2)

    score = 3.0 * math.log1p(ccu) + 2.0 * math.log1p(fol)
    if d_fol >= 10:
        score += min(8.0, 1.3 * math.log1p(d_fol))
    if d_ccu >= 5:
        score += min(4.0, 0.9 * math.log1p(d_ccu))
    if has_schinese:
        score += 2.0
    if has_trailer:
        score += 1.0
    if news >= 1:
        score += 1.5
    if from_wishlist:
        score += 1.5

    credits = {str(x).strip().lower() for x in (developers or []) + (publishers or []) if str(x).strip()}
    if credits & INDIE_PUBLISHER_WHITELIST:
        score += 2.5

    if revs >= 5 and pos >= 80.0:
        velocity = revs / max(1, age_days if age_days is not None else 14)
        score += min(6.0, 1.5 * math.log1p(velocity * 5.0))

    if age_days is not None and has_traction:
        if age_days <= 3:
            score += 3.0
        elif age_days <= 7:
            score += 2.0
        elif age_days <= 14:
            score += 1.0

    if has_ai_disclosure:
        score -= 2.0

    return round(max(0.0, score), 2)


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
