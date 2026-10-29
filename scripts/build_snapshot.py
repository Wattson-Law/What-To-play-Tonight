from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coop_night_picker.constants import CATEGORY_ZH, HORROR_TAG_IDS, REVIEW_TIERS
from coop_night_picker.db import init_db, insert_quote, upsert_game
from coop_night_picker.extractor import (
    build_avoid_reasons,
    derive_review_summary,
    extract_quotes,
    parse_appdetails,
    parse_appreviews,
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
    merged = {}
    root = Path(args.offline_fixture) if args.offline_fixture else None
    client = SteamClient()

    if root:
        for file_path in sorted(root.glob("*")):
            if file_path.suffix.lower() == ".html":
                for item in parse_search_html(file_path.read_text(encoding="utf8")):
                    merged[item["appid"]] = item
            elif file_path.suffix.lower() == ".json":
                payload = json.loads(file_path.read_text(encoding="utf8"))
                appid = payload.get("appid") or (int(file_path.stem) if file_path.stem.isdigit() else None)
                if appid:
                    merged[int(appid)] = payload
                elif isinstance(payload, dict):
                    for key, value in payload.items():
                        if str(key).isdigit() and isinstance(value, dict) and ("data" in value or "success" in value):
                            merged[int(key)] = {"appid": int(key), "appdetails": value}
    else:
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
                merged[int(item["appid"])] = item

    selected = list(merged.items())[: args.limit]

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

    for appid, seed in selected:
        game = dict(seed.get("game", seed))
        game["appid"] = appid
        seed_tag_ids = set(seed.get("tag_ids", []))

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
        game.update(details)
        game["has_demo"] = int(
            bool(game.get("has_demo"))
            or game.get("type") == "demo"
            or bool(game.get("demo_appid"))
            or bool(game.get("fullgame_appid"))
        )

        store_raw = fixture(appid, "store") or fixture(appid, "store_page")
        page = parse_store_page(store_raw or (client.store_page(appid) if not root else ""))
        tags = page["tags"]
        tag_ids = {tag["id"] for tag in tags}
        game["tags_json"] = [tag["name"] for tag in tags]
        all_tag_ids = tag_ids | seed_tag_ids
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
        game["online_coop"] = int(bool({9, 38} & category_ids or {1685, 3843, 4508} & all_tag_ids or "在线合作" in category_names))
        game["local_coop"] = int(bool({24, 39, 48} & category_ids or {3841, 7368} & all_tag_ids or "同屏/分屏合作" in category_names or "本地合作" in category_names))
        game["remote_play"] = int(bool(44 in category_ids or "远程同乐" in category_names))

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

        game["avoid_reasons_json"] = build_avoid_reasons(negative_reviews)
        game["negative_match_reviews"] = sum(
            1
            for review in negative_reviews
            if any(dimension in {"network", "dealbreaker"} for quote in extract_quotes(review, False, 99) for dimension in quote["dimensions_json"])
        )
        score = game["review_score"]
        game["is_avoid"] = int(
            (1 <= score <= 5)
            or (
                game.get("schinese_pct") is not None
                and game.get("positive_pct") is not None
                and game["positive_pct"] - game["schinese_pct"] >= 15
            )
            or game["negative_match_reviews"] >= 3
        )

        if not args.dry_run:
            upsert_game(conn, game)
            conn.execute("DELETE FROM game_quotes WHERE appid=?", (appid,))
            conn.commit()
            for review_group, voted_up, cap in ((positive_reviews, True, 3), (negative_reviews, False, 2)):
                quotes = []
                for review in review_group:
                    quotes.extend(extract_quotes(review, voted_up, 99, appid=appid))
                for quote in quotes[:cap]:
                    if quote.get("review_url", "").startswith("https://steamcommunity.com/profiles/"):
                        quote["appid"] = appid
                        insert_quote(conn, quote)

    if not args.dry_run and selected:
        placeholders = ",".join("?" for _ in selected)
        active_ids = [appid for appid, _ in selected]
        conn.execute(f"DELETE FROM game_quotes WHERE appid NOT IN ({placeholders})", active_ids)
        conn.execute(f"DELETE FROM games WHERE appid NOT IN ({placeholders})", active_ids)
        conn.commit()

    processed = len(selected)
    conn.close()
    print(f"processed {processed} games; db={args.db}; dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
