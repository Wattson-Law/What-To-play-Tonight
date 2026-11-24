from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coop_night_picker.constants import (
    CATEGORY_ZH,
    CURATOR_SOURCES,
    DEMO_RADAR_FEEDS,
    FRIENDSLOP_FEEDS,
    QUIRKY_TAG_IDS,
    REVIEW_TIERS,
    TAG_ZH,
)
from coop_night_picker.db import (
    get_previous_demo_metric,
    init_db,
    insert_quote,
    is_playable_game,
    prune_demo_metrics,
    record_demo_metric,
    upsert_game,
)
from coop_night_picker.extractor import (
    build_avoid_reasons,
    compute_potential_score,
    derive_review_summary,
    extract_quotes,
    infer_max_players,
    parse_appdetails,
    parse_appreviews,
    parse_curator_recommendations,
    parse_release_age_days,
    parse_search_html,
    parse_store_page,
)
from coop_night_picker.steam_client import SteamClient


def _has_cjk(text: str | None) -> bool:
    return bool(text) and any("\u4e00" <= ch <= "\u9fff" for ch in str(text))


def translate_english_descriptions(items: list[tuple[int, str, str]], max_items: int = 25) -> dict[int, str]:
    """Batch-generate crisp 1-sentence Chinese gameplay summaries for English-only Steam descriptions."""
    if not items:
        return {}
    api_key = (os.getenv("LLM_API_KEY") or os.getenv("GITHUB_TOKEN") or "").strip()
    if not api_key:
        return {}
    try:
        import httpx
    except ImportError:
        return {}

    base_url = (os.getenv("LLM_BASE_URL") or "https://models.github.ai/inference").rstrip("/")
    default_model = "openai/gpt-4o-mini" if "models.github.ai" in base_url else "gpt-4o-mini"
    model = os.getenv("LLM_MODEL") or default_model
    batch = items[: max(1, int(max_items))]
    payload_lines = [{"appid": int(aid), "name": name, "desc": desc[:260]} for aid, name, desc in batch]
    prompt = (
        "你是Steam联机游戏编辑。请把下面每款英文游戏简介总结成一句简洁自然的简体中文核心玩法安利（20~42字，不要剧透、不要加引号、保留核心玩法与联机亮点）。"
        "严格输出JSON对象，键为字符串appid，值为中文一句话简介，不要输出任何额外解释或代码块。"
    )
    try:
        with httpx.Client(timeout=30.0) as hc:
            resp = hc.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(payload_lines, ensure_ascii=False)},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1400,
                },
            )
            if resp.status_code != 200:
                return {}
            content = resp.json()["choices"][0]["message"]["content"].strip()
            m = re.search(r"\{.*\}", content, re.S)
            if not m:
                return {}
            parsed = json.loads(m.group(0))
            if not isinstance(parsed, dict):
                return {}
            out: dict[int, str] = {}
            for k, v in parsed.items():
                if str(k).isdigit() and isinstance(v, str) and v.strip():
                    out[int(k)] = " ".join(v.split()).strip()[:80]
            return out
    except Exception:
        return {}


def _items(raw):
    if isinstance(raw, dict):
        return raw.get("items") or parse_search_html(raw.get("results_html") or raw.get("html") or raw.get("text", ""))
    return parse_search_html(raw)


def _review_payload(raw):
    return parse_appreviews(json.loads(raw)) if raw else {"reviews": []}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--db", default="data/games.sqlite")
    parser.add_argument("--with-reviews", action="store_true")
    parser.add_argument("--offline-fixture")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    conn = init_db(args.db)
    today_str = datetime.date.today().isoformat()
    # Preserve previous snapshot followers & cached Chinese summaries across daily rebuilds
    prev_games: dict[int, dict] = {}
    for r in conn.execute("SELECT appid, followers, short_desc, short_desc_zh FROM games").fetchall():
        prev_games[int(r["appid"])] = dict(r)
    pending_translations: list[tuple[int, str, str]] = []

    merged: dict[int, dict] = {}
    curator_map: dict[int, list[dict]] = {}
    radar_ids: set[int] = set()
    wishlist_ids: set[int] = set()
    root = Path(args.offline_fixture) if args.offline_fixture else None
    client = SteamClient()

    def _register_curator_items(items: list[dict]):
        for item in items:
            aid = int(item["appid"])
            curator_map.setdefault(aid, []).append(item)
            if aid not in merged:
                merged[aid] = {
                    "appid": aid,
                    "name": item.get("name", ""),
                    "tag_ids": list(item.get("tag_ids", [])),
                    "price_cny": item.get("price_cny", 0.0),
                    "is_free": item.get("is_free", 0),
                    "has_demo": item.get("has_demo", 0),
                    "discount_pct": item.get("discount_pct", 0),
                    "header_image": item.get("header_image", ""),
                    "max_players": item.get("max_players"),
                }
            else:
                existing_tags = set(merged[aid].get("tag_ids", [])) | set(item.get("tag_ids", []))
                merged[aid]["tag_ids"] = sorted(existing_tags)
                if not merged[aid].get("max_players") and item.get("max_players"):
                    merged[aid]["max_players"] = item["max_players"]

    if root:
        for file_path in sorted(root.glob("*")):
            if file_path.suffix.lower() == ".html":
                text = file_path.read_text(encoding="utf8")
                if "class=\"recommendation\"" in text or "curator_clanid" in text:
                    _register_curator_items(parse_curator_recommendations(text, curator_name=file_path.stem))
                for item in parse_search_html(text):
                    merged.setdefault(int(item["appid"]), item)
            elif file_path.suffix.lower() == ".json":
                payload = json.loads(file_path.read_text(encoding="utf8"))
                appid = payload.get("appid") or (int(file_path.stem) if file_path.stem.isdigit() else None)
                if appid:
                    merged[int(appid)] = payload
                elif isinstance(payload, dict):
                    if "results_html" in payload and ("recommendation" in str(payload.get("results_html", ""))):
                        _register_curator_items(parse_curator_recommendations(payload, curator_name=file_path.stem))
                    for key, value in payload.items():
                        if str(key).isdigit() and isinstance(value, dict) and ("data" in value or "success" in value):
                            merged[int(key)] = {"appid": int(key), "appdetails": value}
    else:
        # 0. Top priority: Automated 4-channel Friendslop / Quirky Co-op & Popular Wishlist feeds
        for idx, feed in enumerate(FRIENDSLOP_FEEDS, 1):
            feed_params = {k: v for k, v in feed.items() if k != "name"}
            is_wishlist_feed = feed.get("filter") == "popularwishlist"
            is_demo_feed = feed.get("category1") == 10
            for item in _items(client.store_search("", count=min(50, max(30, args.limit)), **feed_params)):
                aid = int(item["appid"])
                if is_wishlist_feed:
                    wishlist_ids.add(aid)
                if is_demo_feed:
                    radar_ids.add(aid)
                if aid not in merged:
                    merged[aid] = dict(item)
                else:
                    merged[aid]["tag_ids"] = sorted(set(merged[aid].get("tag_ids", [])) | set(item.get("tag_ids", [])))
                    if not merged[aid].get("release_date_text") and item.get("release_date_text"):
                        merged[aid]["release_date_text"] = item["release_date_text"]
                curator_map.setdefault(aid, []).append(
                    {
                        "appid": aid,
                        "name": item.get("name", ""),
                        "tag_ids": list(item.get("tag_ids", [])),
                        "curator_name": feed["name"],
                        "clanid": 990000 + idx,
                        "voted_up": 1,
                        "attitude": "recommended",
                        "blurb": "",
                        "quote_text": "",
                        "review_url": f"https://store.steampowered.com/app/{aid}/",
                    }
                )

        # 0.5 Early-Stage Demo Radar feeds: catch 1-7 day old co-op Demos BEFORE crowdsourced tags exist
        for feed in DEMO_RADAR_FEEDS:
            feed_params = {k: v for k, v in feed.items() if k != "name"}
            for item in _items(client.store_search("", count=30, **feed_params)):
                aid = int(item["appid"])
                radar_ids.add(aid)
                if aid not in merged:
                    merged[aid] = dict(item)
                else:
                    merged[aid]["tag_ids"] = sorted(set(merged[aid].get("tag_ids", [])) | set(item.get("tag_ids", [])))
                    if not merged[aid].get("release_date_text") and item.get("release_date_text"):
                        merged[aid]["release_date_text"] = item["release_date_text"]

        # 1. Human Curator recommendations (Co-op, Online Co-op)
        for curator in CURATOR_SOURCES:
            for tag_filter in ("3843", "1685"):
                raw_cur = client.curator_recommendations(
                    curator["clanid"],
                    tagids=tag_filter,
                    start=0,
                    count=min(50, max(20, args.limit)),
                )
                c_items = parse_curator_recommendations(
                    raw_cur,
                    curator_name=curator["name"],
                    clanid=curator["clanid"],
                )
                _register_curator_items(c_items)

        # 2. Supplementary store search to ensure Demo / Free / Horror sample coverage
        for query in (
            {"category1": 998, "category3": 38},
            {"category1": 10, "category3": 38},
            {"category1": 998, "tags": 3843},
            {"category1": 998, "tags": 1685},
            {"category1": 998, "tags": 1667},
            {"category1": 998, "tags": 1721},
            {"category1": 998, "tags": 3978},
        ):
            for item in _items(client.store_search("", count=args.limit, **query)):
                aid = int(item["appid"])
                if aid not in merged:
                    merged[aid] = item
                else:
                    merged[aid]["tag_ids"] = sorted(set(merged[aid].get("tag_ids", [])) | set(item.get("tag_ids", [])))

    # Order candidates so quirky/Friendslop & multi-curator Picks come first,
    # and reserve a dedicated Demo Radar slice for fresh high-potential Demos.
    ordered_candidates = sorted(
        merged.items(),
        key=lambda kv: (
            len({c["curator_name"] for c in curator_map.get(kv[0], []) if c.get("voted_up")}),
            int(bool(set(kv[1].get("tag_ids", [])) & QUIRKY_TAG_IDS)),
            kv[0],
        ),
        reverse=True,
    ) if not root else list(merged.items())
    radar_quota = min(30, max(10, args.limit // 6)) if (not root and args.limit >= 50) else 0
    main_limit = args.limit - radar_quota
    main_pool = ordered_candidates[: max(main_limit * 2, main_limit + 60)] if not root else ordered_candidates[: args.limit]
    radar_only_pool = (
        sorted(
            [(aid, merged[aid]) for aid in radar_ids if aid in merged],
            key=lambda kv: (
                int(parse_release_age_days(kv[1].get("release_date_text")) is not None and (parse_release_age_days(kv[1].get("release_date_text")) or 999) <= 14),
                kv[0],
            ),
            reverse=True,
        )[:45]
        if radar_quota > 0
        else []
    )
    kept_ids: list[int] = []
    kept_linked_ids: set[int] = set()
    processed_ids: set[int] = set()
    consecutive_review_failures = 0
    appreviews_disabled = False

    def fixture(appid, kind):
        if not root:
            return None
        for name in (
            f"{appid}_{kind}.json",
            f"{appid}_{kind}.html",
            f"{appid}.{kind}.json",
            f"{appid}.{kind}.html",
        ):
            path = root / name
            if path.exists():
                return path.read_text(encoding="utf8")
        return None

    def _enrich_and_persist(appid: int, seed: dict, *, is_radar_slice: bool = False) -> bool:
        nonlocal consecutive_review_failures, appreviews_disabled
        if appid in processed_ids or appid in kept_linked_ids:
            return False
        processed_ids.add(appid)
        game = dict(seed.get("game", seed))
        game["appid"] = appid
        seed_tag_ids = set(seed.get("tag_ids", []))
        c_entries = curator_map.get(appid, [])
        for ce in c_entries:
            seed_tag_ids.update(ce.get("tag_ids", []))

        appdetails_raw = fixture(appid, "appdetails")
        details = (
            parse_appdetails(json.loads(appdetails_raw), appid)
            if appdetails_raw
            else parse_appdetails(seed.get("appdetails"), appid)
            if seed.get("appdetails")
            else parse_appdetails(client.appdetails(appid), appid)
            if not root
            else {}
        )
        # Keep non-empty seed fields if appdetails was empty or partial
        for k, v in details.items():
            if v is not None and v != "" and v != []:
                game[k] = v
            elif k not in game:
                game[k] = v

        # Skip if linked fullgame/demo pair was already kept (avoid duplicate slots for same title)
        linked_ids = {appid}
        if game.get("demo_appid"):
            linked_ids.add(int(game["demo_appid"]))
        if game.get("fullgame_appid"):
            linked_ids.add(int(game["fullgame_appid"]))
        if linked_ids & kept_linked_ids:
            return False

        game["has_demo"] = int(
            bool(game.get("has_demo"))
            or game.get("type") == "demo"
            or bool(game.get("demo_appid"))
            or bool(game.get("fullgame_appid"))
        )

        # Skip unreleased games that have neither a free release nor a downloadable demo
        if game.get("coming_soon") and not game.get("is_free") and not game["has_demo"]:
            return False

        # Developer-declared Steam categories are ground truth for multiplayer support:
        # exclude pure single-player games even if crowdsourced tags were trolled.
        category_ids = set(game.get("category_ids") or [])
        multiplayer_cat_ids = {1, 9, 20, 24, 27, 36, 37, 38, 39, 44, 47, 48, 49}
        explicit_single_only = bool(category_ids) and (2 in category_ids) and not (multiplayer_cat_ids & category_ids)
        if explicit_single_only:
            return False

        store_raw = fixture(appid, "store") or fixture(appid, "store_page")
        page = parse_store_page(store_raw or (client.store_page(appid) if not root else ""))
        game["has_ai_disclosure"] = int(page.get("has_ai_disclosure", 0))
        tags = page["tags"]
        tag_ids = {tag["id"] for tag in tags}
        all_tag_ids = tag_ids | seed_tag_ids
        # Use only Steam's top-8 highest-voted tags for horror classification to ignore low-vote troll tags (e.g. #17 心理恐怖)
        horror_tag_pool = ({tag["id"] for tag in tags[:8]} | seed_tag_ids) if tags else seed_tag_ids

        tag_names = []
        seen_t = set()
        for t_name in (
            [tag["name"] for tag in tags]
            + [TAG_ZH[tid] for tid in sorted(all_tag_ids) if tid in TAG_ZH]
            + list(game.get("genre_names") or [])
        ):
            if t_name and t_name not in seen_t:
                seen_t.add(t_name)
                tag_names.append(t_name)
        game["tags_json"] = tag_names

        if {1667, 3978} & horror_tag_pool:
            game["horror_level"] = "scary"
        elif 1721 in horror_tag_pool:
            game["horror_level"] = "mild"
        else:
            game["horror_level"] = "none"

        category_names = set(game.get("categories_json") or [])
        category_names.update(CATEGORY_ZH[category_id] for category_id in category_ids if category_id in CATEGORY_ZH)
        game["categories_json"] = sorted(category_names)
        game["online_coop"] = int(
            bool({9, 38} & category_ids or {1685, 3843, 4508} & all_tag_ids or "在线合作" in category_names or c_entries or is_radar_slice)
        )
        game["local_coop"] = int(
            bool(
                {24, 39, 48} & category_ids
                or {3841, 7368} & all_tag_ids
                or "同屏/分屏合作" in category_names
                or "本地合作" in category_names
            )
        )
        game["remote_play"] = int(bool(44 in category_ids or "远程同乐" in category_names))

        if not game.get("max_players"):
            blurb_blob = " ".join(c.get("blurb", "") for c in c_entries)
            game["max_players"] = infer_max_players(
                f"{game.get('short_desc') or ''} {blurb_blob}",
                categories=game["categories_json"],
                tags=game["tags_json"],
            )

        pos_curators = []
        neg_curators = []
        for ce in c_entries:
            if ce.get("voted_up"):
                if ce["curator_name"] not in pos_curators:
                    pos_curators.append(ce["curator_name"])
            else:
                if ce["curator_name"] not in neg_curators:
                    neg_curators.append(ce["curator_name"])
        game["curator_count"] = len(pos_curators)
        game["curators_json"] = pos_curators

        summary = derive_review_summary(page["review_props"])
        global_summary = summary.get("global", {})
        schinese_summary = summary.get("schinese", {})
        game["total_reviews"] = global_summary.get("total", game.get("total_reviews", 0))
        game["positive_pct"] = global_summary.get("pct", game.get("positive_pct", 0))
        game["schinese_total"] = schinese_summary.get("total", schinese_summary.get("total_reviews"))
        game["schinese_pct"] = schinese_summary.get("pct", schinese_summary.get("positive_pct"))
        game["review_score"] = int(global_summary.get("score") or game.get("review_score") or 0)
        game["schinese_score"] = schinese_summary.get("score")
        game["review_tier_zh"] = REVIEW_TIERS.get(game["review_score"], REVIEW_TIERS[0])["zh"]

        # Final playability check: exclude titles that cannot be downloaded/played tonight
        if not is_playable_game(game):
            return False

        # Early Demo Radar enrichment (CCU on Demo, followers & recent devlogs on fullgame)
        raw_rel = game.get("release_date")
        if isinstance(raw_rel, dict):
            raw_rel = raw_rel.get("date")
        release_str = str(raw_rel or seed.get("release_date_text") or "").strip()
        game["release_date"] = release_str
        age_days = parse_release_age_days(release_str)
        game["release_age_days"] = age_days

        # Exclude old fullgames released >90 days ago that merely happen to have an ancient Demo
        is_recent_or_unreleased_fullgame_demo = (
            bool(game["has_demo"])
            and (age_days is None or age_days <= 90 or bool(game.get("coming_soon")))
        )
        is_demo_candidate = game.get("type") == "demo" or appid in radar_ids or is_recent_or_unreleased_fullgame_demo

        prev_row = prev_games.get(appid) or {}
        short_desc_now = str(game.get("short_desc") or "").strip()
        if prev_row.get("short_desc_zh") and str(prev_row.get("short_desc") or "").strip() == short_desc_now:
            game["short_desc_zh"] = prev_row["short_desc_zh"]
        elif short_desc_now and not _has_cjk(short_desc_now):
            pending_translations.append((appid, str(game.get("name") or ""), short_desc_now))

        early_raw = fixture(appid, "early")
        ccu = 0
        followers = 0
        recent_news = 0
        if early_raw:
            early_obj = json.loads(early_raw)
            ccu = int(early_obj.get("current_players") or 0)
            followers = int(early_obj.get("followers") or 0)
            recent_news = int(early_obj.get("recent_news_count") or 0)
        elif not root and is_demo_candidate:
            ccu_target = int(game["demo_appid"]) if game.get("demo_appid") else appid
            hub_target = int(game["fullgame_appid"]) if game.get("fullgame_appid") else appid
            ccu = int(client.current_players(ccu_target) or 0)
            live_followers = client.community_followers(hub_target)
            if live_followers is None:
                # Preserve previous follower count if Steam Community rate-limited (HTTP 429)
                followers = int(prev_row.get("followers") or 0)
            else:
                followers = int(live_followers)
            news_items = client.app_news(hub_target, count=5)
            cutoff_ts = time.time() - 14 * 86400
            recent_news = sum(1 for item in news_items if float(item.get("date") or 0) >= cutoff_ts)

        prev_metric = get_previous_demo_metric(conn, appid, today_str) if is_demo_candidate else None
        delta_ccu = max(0, ccu - int(prev_metric["current_players"] or 0)) if prev_metric else 0
        delta_followers = max(0, followers - int(prev_metric["followers"] or 0)) if prev_metric else 0

        potential_score = compute_potential_score(
            current_players=ccu,
            followers=followers,
            age_days=age_days,
            recent_news_count=recent_news,
            has_schinese=game.get("has_schinese", 0),
            has_trailer=game.get("has_trailer", 0),
            has_ai_disclosure=game.get("has_ai_disclosure", 0),
            total_reviews=game.get("total_reviews", 0),
            positive_pct=game.get("positive_pct", 0.0),
            developers=game.get("developers_json") or [],
            publishers=game.get("publishers_json") or [],
            from_wishlist=appid in wishlist_ids,
            delta_followers=delta_followers,
            delta_ccu=delta_ccu,
        )
        game["current_players"] = ccu
        game["followers"] = followers
        game["delta_ccu"] = delta_ccu
        game["delta_followers"] = delta_followers
        game["recent_news_count"] = recent_news
        game["potential_score"] = potential_score
        game["is_demo_radar"] = int(is_demo_candidate)

        # For the dedicated radar slice, drop zero-traction noise Demos so only real gems take quota
        if is_radar_slice and potential_score < 6.0 and int(game.get("total_reviews") or 0) < 5:
            return False

        positive_reviews = []
        negative_reviews = []
        if args.with_reviews:
            positive_raw = fixture(appid, "reviews_positive")
            negative_raw = fixture(appid, "reviews_negative")
            if positive_raw:
                positive_reviews = _review_payload(positive_raw)["reviews"]
            elif not root and not appreviews_disabled:
                raw_pos = client.appreviews(appid, review_type="positive")
                if isinstance(raw_pos, dict) and raw_pos.get("unavailable"):
                    consecutive_review_failures += 1
                    if consecutive_review_failures >= 3:
                        appreviews_disabled = True
                        print("appreviews endpoint unavailable (3 consecutive failures); skipping remaining review fetches", flush=True)
                else:
                    consecutive_review_failures = 0
                    positive_reviews = parse_appreviews(raw_pos)["reviews"]
            if negative_raw:
                negative_reviews = _review_payload(negative_raw)["reviews"]
            elif not root and not appreviews_disabled:
                raw_neg = client.appreviews(appid, review_type="negative")
                if isinstance(raw_neg, dict) and raw_neg.get("unavailable"):
                    consecutive_review_failures += 1
                    if consecutive_review_failures >= 3:
                        appreviews_disabled = True
                        print("appreviews endpoint unavailable (3 consecutive failures); skipping remaining review fetches", flush=True)
                else:
                    consecutive_review_failures = 0
                    negative_reviews = parse_appreviews(raw_neg)["reviews"]
            if not positive_raw and not negative_raw and root:
                all_raw = fixture(appid, "reviews")
                all_reviews = _review_payload(all_raw)["reviews"] if all_raw else []
                positive_reviews = [review for review in all_reviews if review.get("voted_up")]
                negative_reviews = [review for review in all_reviews if not review.get("voted_up")]

        avoid_reasons = build_avoid_reasons(negative_reviews)
        for nc in neg_curators:
            avoid_reasons.append(f"鉴赏家避雷({nc})")
        schinese_gap = (
            game.get("schinese_pct") is not None
            and game.get("positive_pct") is not None
            and (game.get("schinese_total") is None or int(game["schinese_total"]) >= 10)
            and float(game["schinese_pct"]) < 70.0
            and (float(game["positive_pct"]) - float(game["schinese_pct"]) >= 15.0)
        )
        if schinese_gap:
            avoid_reasons.append(
                f"简中好评率偏低({float(game['schinese_pct']):.0f}% vs 全语言{float(game['positive_pct']):.0f}%)"
            )
        score = game["review_score"]
        if 1 <= score <= 5 and not avoid_reasons:
            pct = float(game.get("positive_pct") or 0)
            avoid_reasons.append(f"{game['review_tier_zh']}({pct:.0f}%)" if pct > 0 else game["review_tier_zh"])
        game["avoid_reasons_json"] = avoid_reasons
        game["negative_match_reviews"] = sum(
            1
            for review in negative_reviews
            if any(
                dimension in {"network", "dealbreaker"}
                for quote in extract_quotes(review, False, 99)
                for dimension in quote["dimensions_json"]
            )
        )
        human_pos_curators = [c for c in pos_curators if not str(c).startswith("Steam")]
        game["is_avoid"] = int(
            (1 <= score <= 5)
            or schinese_gap
            or game["negative_match_reviews"] >= 3
            or (len(neg_curators) > 0 and len(pos_curators) == 0)
            or (len(neg_curators) > 0 and len(neg_curators) >= len(human_pos_curators) and not (all_tag_ids & QUIRKY_TAG_IDS))
        )

        kept_ids.append(appid)
        kept_linked_ids.update(linked_ids)
        if not args.dry_run:
            upsert_game(conn, game)
            if is_demo_candidate and (ccu > 0 or followers > 0):
                record_demo_metric(conn, appid, today_str, current_players=ccu, followers=followers)
            conn.execute("DELETE FROM game_quotes WHERE appid=?", (appid,))
            conn.commit()
            # Deduplicate curator quotes and preserve both positive and negative curator blurbs
            seen_quotes: set[str] = set()
            pos_curator_quotes: list[dict] = []
            neg_curator_quotes: list[dict] = []
            for ce in c_entries:
                q_text = (ce.get("quote_text") or "").strip()
                if not q_text or q_text in seen_quotes:
                    continue
                seen_quotes.add(q_text)
                if ce.get("voted_up", 1):
                    pos_curator_quotes.append(ce)
                else:
                    neg_curator_quotes.append(ce)
            for ce in pos_curator_quotes[:2] + neg_curator_quotes[:1]:
                insert_quote(
                    conn,
                    {
                        "appid": appid,
                        "recommendation_id": f"curator-{ce.get('clanid', 0)}",
                        "voted_up": int(ce.get("voted_up", 1)),
                        "votes_helpful": 999 if ce.get("voted_up") else 500,
                        "playtime_hours": 0.0,
                        "quote_text": ce["quote_text"],
                        "dimensions_json": ce.get("dimensions_json", ["coop_quality"]),
                        "review_url": ce["review_url"],
                    },
                )
            for review_group, voted_up, cap in ((positive_reviews, True, 3), (negative_reviews, False, 2)):
                quotes = []
                for review in review_group:
                    quotes.extend(extract_quotes(review, voted_up, 99, appid=appid))
                for quote in quotes[:cap]:
                    if quote.get("review_url", "").startswith("https://steamcommunity.com/profiles/"):
                        quote["appid"] = appid
                        insert_quote(conn, quote)
        return True

    for appid, seed in main_pool:
        if len(kept_ids) >= main_limit:
            break
        _enrich_and_persist(appid, seed, is_radar_slice=False)

    radar_added = 0
    for appid, seed in radar_only_pool:
        if radar_added >= radar_quota or len(kept_ids) >= args.limit:
            break
        if _enrich_and_persist(appid, seed, is_radar_slice=True):
            radar_added += 1

    # Fill any remaining unused radar quota from main_pool if needed
    for appid, seed in main_pool:
        if len(kept_ids) >= args.limit:
            break
        _enrich_and_persist(appid, seed, is_radar_slice=False)

    if not args.dry_run and kept_ids:
        kept_set = set(kept_ids)
        to_translate = [item for item in pending_translations if item[0] in kept_set]
        if to_translate and not root:
            translated = translate_english_descriptions(to_translate, max_items=25)
            for aid, zh_desc in translated.items():
                conn.execute("UPDATE games SET short_desc_zh=? WHERE appid=?", (zh_desc, int(aid)))
        placeholders = ",".join("?" for _ in kept_ids)
        conn.execute(f"DELETE FROM game_quotes WHERE appid NOT IN ({placeholders})", kept_ids)
        conn.execute(f"DELETE FROM games WHERE appid NOT IN ({placeholders})", kept_ids)
        prune_demo_metrics(conn, keep_dates=14)
        conn.commit()

    processed = len(kept_ids)
    conn.close()
    print(f"processed {processed} games (radar_slice={radar_added}); db={args.db}; dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
