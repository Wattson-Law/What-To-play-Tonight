from __future__ import annotations

import argparse
import json
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
from coop_night_picker.db import init_db, insert_quote, is_playable_game, upsert_game
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
    processed_ids: set[int] = set()

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
        if appid in processed_ids:
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

        game["has_demo"] = int(
            bool(game.get("has_demo"))
            or game.get("type") == "demo"
            or bool(game.get("demo_appid"))
            or bool(game.get("fullgame_appid"))
        )

        # Skip unreleased games that have neither a free release nor a downloadable demo
        if game.get("coming_soon") and not game.get("is_free") and not game["has_demo"]:
            return False

        store_raw = fixture(appid, "store") or fixture(appid, "store_page")
        page = parse_store_page(store_raw or (client.store_page(appid) if not root else ""))
        game["has_ai_disclosure"] = int(page.get("has_ai_disclosure", 0))
        tags = page["tags"]
        tag_ids = {tag["id"] for tag in tags}
        all_tag_ids = tag_ids | seed_tag_ids

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

        if {1667, 3978} & all_tag_ids:
            game["horror_level"] = "scary"
        elif 1721 in all_tag_ids:
            game["horror_level"] = "mild"
        else:
            game["horror_level"] = "none"

        category_ids = set(game.get("category_ids") or [])
        category_names = set(game.get("categories_json") or [])
        category_names.update(CATEGORY_ZH[category_id] for category_id in category_ids if category_id in CATEGORY_ZH)
        game["categories_json"] = sorted(category_names)
        explicit_single_only = (
            bool(category_ids)
            and (2 in category_ids)
            and not ({1, 9, 20, 27, 36, 38, 44, 47, 48, 49} & category_ids)
            and not ({1685, 3843, 4508} & all_tag_ids)
        )
        game["online_coop"] = int(
            not explicit_single_only
            and bool({9, 38} & category_ids or {1685, 3843, 4508} & all_tag_ids or "在线合作" in category_names or c_entries or is_radar_slice)
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

        is_demo_candidate = bool(game["has_demo"]) or game.get("type") == "demo" or appid in radar_ids
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
            followers = int(client.community_followers(hub_target) or 0)
            news_items = client.app_news(hub_target, count=5)
            cutoff_ts = time.time() - 14 * 86400
            recent_news = sum(1 for item in news_items if float(item.get("date") or 0) >= cutoff_ts)

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
        )
        game["current_players"] = ccu
        game["followers"] = followers
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
            elif not root:
                positive_reviews = _review_payload(json.dumps(client.appreviews(appid, review_type="positive")))["reviews"]
            if negative_raw:
                negative_reviews = _review_payload(negative_raw)["reviews"]
            elif not root:
                negative_reviews = _review_payload(json.dumps(client.appreviews(appid, review_type="negative")))["reviews"]
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
            and (float(game["positive_pct"]) - float(game["schinese_pct"]) >= 15.0)
        )
        if schinese_gap:
            avoid_reasons.append(
                f"简中好评率偏低({float(game['schinese_pct']):.0f}% vs 全语言{float(game['positive_pct']):.0f}%)"
            )
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
        score = game["review_score"]
        human_pos_curators = [c for c in pos_curators if not str(c).startswith("Steam")]
        game["is_avoid"] = int(
            (1 <= score <= 5)
            or schinese_gap
            or game["negative_match_reviews"] >= 3
            or (len(neg_curators) > 0 and len(pos_curators) == 0)
            or (len(neg_curators) > 0 and len(neg_curators) >= len(human_pos_curators) and not (all_tag_ids & QUIRKY_TAG_IDS))
        )

        kept_ids.append(appid)
        if not args.dry_run:
            upsert_game(conn, game)
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
        placeholders = ",".join("?" for _ in kept_ids)
        conn.execute(f"DELETE FROM game_quotes WHERE appid NOT IN ({placeholders})", kept_ids)
        conn.execute(f"DELETE FROM games WHERE appid NOT IN ({placeholders})", kept_ids)
        conn.commit()

    processed = len(kept_ids)
    conn.close()
    print(f"processed {processed} games (radar_slice={radar_added}); db={args.db}; dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
