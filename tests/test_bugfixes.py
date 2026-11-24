"""Regression & TDD tests for data parsing, playability filtering, search, and KOOK bot stability."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coop_night_picker import db, extractor, kook_bot, server
from coop_night_picker.steam_client import SteamClient
from scripts import build_snapshot


class ExtractorBugfixTests(unittest.TestCase):
    def test_parse_store_page_handles_apostrophe_in_data_props_and_multi_arg_tag_modal(self):
        store_html = """
        <script type="text/javascript">
        $J( function() {
            InitAppTagModal( 4000,
                [{"tagid":3810,"name":"沙盒","count":19205,"browseable":true},{"tagid":4136,"name":"欢乐","count":13302,"browseable":true},{"tagid":1721,"name":"心理恐怖","count":200,"browseable":true}],
                [],
                "https://store.steampowered.com/app/4000/"
            );
        });
        </script>
        <div class="" data-featuretarget="appreviews" data-props="{&quot;appid&quot;:4000,&quot;appname&quot;:&quot;Garry's Mod&quot;,&quot;summary_options&quot;:{&quot;summaryGlobalNoOutliers&quot;:{&quot;nReviews&quot;:1000000,&quot;nReviewsPositive&quot;:965000,&quot;fRating&quot;:96.5,&quot;eReviewScore&quot;:9}},&quot;filter_options&quot;:{&quot;rgLanguageOutliers&quot;:[{&quot;strLanguage&quot;:&quot;schinese&quot;,&quot;nReviews&quot;:120000,&quot;fRating&quot;:95.2,&quot;eReviewScore&quot;:9}]}}"></div>
        """
        parsed = extractor.parse_store_page(store_html)
        tag_ids = {t["id"] for t in parsed["tags"]}
        self.assertEqual(tag_ids, {3810, 4136, 1721})
        self.assertEqual(len(parsed["review_props"]), 1)
        summary = extractor.derive_review_summary(parsed["review_props"])
        self.assertEqual(summary["global"]["score"], 9)
        self.assertEqual(summary["global"]["total"], 1000000)
        self.assertAlmostEqual(summary["schinese"]["pct"], 95.2)

    def test_infer_max_players_ignores_third_person_perspective(self):
        self.assertIsNone(extractor.infer_max_players("《异克斯小队》是一款第三人称 PvE 合作射击游戏。"))
        self.assertIsNone(extractor.infer_max_players("第一人称恐怖生存冒险，与好友并肩作战。"))
        self.assertIsNone(extractor.infer_max_players("以第二人称视角体验独特剧情。"))
        self.assertEqual(extractor.infer_max_players("支持最多 4 人在线联机合作"), 4)
        self.assertEqual(extractor.infer_max_players("一款双人同屏合作解谜游戏"), 2)
        self.assertEqual(extractor.infer_max_players("三人合作小队生存游戏"), 3)

    def test_parse_appdetails_unreleased_price_is_none_and_unescapes_short_desc(self):
        payload = {
            "4534960": {
                "success": True,
                "data": {
                    "steam_appid": 4534960,
                    "name": "Dear Passengers",
                    "type": "game",
                    "is_free": False,
                    "release_date": {"coming_soon": True, "date": "即将宣布"},
                    "short_description": "一款双人合作&quot;友尽&quot;游戏",
                    "categories": [{"id": 38, "description": "在线合作"}],
                    "genres": [{"description": "独立"}],
                },
            }
        }
        parsed = extractor.parse_appdetails(payload, 4534960)
        self.assertIsNone(parsed["price_cny"])
        self.assertEqual(parsed["coming_soon"], 1)
        self.assertEqual(parsed["short_desc"], '一款双人合作"友尽"游戏')

    def test_negative_curator_blurb_ignores_negated_praise(self):
        html_doc = """
        <div>
          <div class="recommendation">
            <a data-ds-appid="892970" data-ds-tagids="[3843,1662]">
              <img src="https://example.com/v.jpg" alt="Valheim">
            </a>
            <span class="color_recommended">推荐</span>
            <div class="recommendation_desc">开放世界联机生存神作，绝不会让你失望，内容扎实绝非半成品！</div>
          </div>
          <div class="recommendation">
            <a data-ds-appid="952060" data-ds-tagids="[1667]">
              <img src="https://example.com/r.jpg" alt="RE3">
            </a>
            <span class="color_recommended">推荐</span>
            <div class="recommendation_desc">赶工作品，场景大量阉割，非常不耐玩，失望。</div>
          </div>
        </div>
        """
        items = extractor.parse_curator_recommendations(html_doc, curator_name="测试鉴赏家", clanid=123)
        by_id = {x["appid"]: x for x in items}
        self.assertEqual(by_id[892970]["voted_up"], 1)
        self.assertEqual(by_id[892970]["attitude"], "recommended")
        self.assertEqual(by_id[952060]["voted_up"], 0)
        self.assertEqual(by_id[952060]["attitude"], "not_recommended")


class PlayabilityAndSnapshotTests(unittest.TestCase):
    def test_unplayable_unreleased_games_are_excluded_from_recommendations(self):
        conn = db.init_db(sqlite3.connect(":memory:"))
        # 1. Released paid game -> playable
        db.upsert_game(
            conn,
            {
                "appid": 100,
                "name": "Playable Paid",
                "online_coop": 1,
                "is_free": 0,
                "has_demo": 0,
                "coming_soon": 0,
                "price_cny": 28.0,
                "review_score": 8,
                "review_tier_zh": "特别好评",
            },
        )
        # 2. Unreleased game WITH playable Demo -> playable
        db.upsert_game(
            conn,
            {
                "appid": 101,
                "name": "Fart Together",
                "online_coop": 1,
                "is_free": 0,
                "has_demo": 1,
                "coming_soon": 1,
                "price_cny": None,
                "review_score": 0,
                "review_tier_zh": "评测不足/试玩区",
            },
        )
        # 3. Unreleased game WITHOUT Demo (coming_soon=1) -> NOT playable
        db.upsert_game(
            conn,
            {
                "appid": 102,
                "name": "Coming Soon No Demo",
                "online_coop": 1,
                "is_free": 0,
                "has_demo": 0,
                "coming_soon": 1,
                "price_cny": None,
                "review_score": 0,
                "review_tier_zh": "评测不足/试玩区",
            },
        )
        # 4. Legacy snapshot row (coming_soon=0, price_cny=0.0, is_free=0, has_demo=0, score=0, reviews=0) -> NOT playable
        db.upsert_game(
            conn,
            {
                "appid": 103,
                "name": "Legacy Unreleased Row",
                "online_coop": 1,
                "is_free": 0,
                "has_demo": 0,
                "price_cny": 0.0,
                "review_score": 0,
                "total_reviews": 0,
                "review_tier_zh": "评测不足/试玩区",
            },
        )

        any_ids = [r["appid"] for r in db.recommend_games(conn, budget="any")]
        under50_ids = [r["appid"] for r in db.recommend_games(conn, budget="under_50")]
        self.assertEqual(set(any_ids), {100, 101})
        self.assertEqual(set(under50_ids), {100, 101})
        conn.close()

    def test_build_snapshot_dedups_quotes_writes_schinese_avoid_reason_and_respects_singleplayer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            # Two curator HTML files containing duplicate entries for appid 2001 (schinese gap >= 15)
            # and single-player-only game 2002
            cur_html = """
            <div>
              <div class="recommendation">
                <a data-ds-appid="2001" data-ds-tagids="[3843]"><img src="" alt="Gap Game"></a>
                <span class="color_recommended">推荐</span>
                <div class="recommendation_desc">很好的四人联机合作游戏，和朋友开黑乐趣满满。</div>
              </div>
              <div class="recommendation">
                <a data-ds-appid="2001" data-ds-tagids="[1685]"><img src="" alt="Gap Game"></a>
                <span class="color_recommended">推荐</span>
                <div class="recommendation_desc">很好的四人联机合作游戏，和朋友开黑乐趣满满。</div>
              </div>
              <div class="recommendation">
                <a data-ds-appid="2002" data-ds-tagids="[492]"><img src="" alt="Solo Inn"></a>
                <span class="color_recommended">推荐</span>
                <div class="recommendation_desc">纯单人模拟经营小游戏。</div>
              </div>
            </div>
            """
            (fixture / "curator_a.html").write_text(cur_html, encoding="utf-8")

            (fixture / "2001_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 2001,
                        "name": "Gap Game",
                        "type": "game",
                        "is_free": False,
                        "price_overview": {"final": 3500, "discount_percent": 0},
                        "release_date": {"coming_soon": False},
                        "categories": [{"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "2001_store.html").write_text(
                '<div data-featuretarget="appreviews" data-props="{&quot;summary_options&quot;:{&quot;summaryGlobalNoOutliers&quot;:{&quot;nReviews&quot;:1000,&quot;fRating&quot;:92.0,&quot;eReviewScore&quot;:9}},&quot;filter_options&quot;:{&quot;rgLanguageOutliers&quot;:[{&quot;strLanguage&quot;:&quot;schinese&quot;,&quot;nReviews&quot;:200,&quot;fRating&quot;:65.0,&quot;eReviewScore&quot;:5}]}}"></div>',
                encoding="utf-8",
            )

            (fixture / "2002_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 2002,
                        "name": "Solo Inn",
                        "type": "game",
                        "is_free": False,
                        "price_overview": {"final": 2000, "discount_percent": 0},
                        "release_date": {"coming_soon": False},
                        "categories": [{"id": 2, "description": "单人"}],
                    }
                ),
                encoding="utf-8",
            )

            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])

            conn = db.init_db(db_path)
            g1 = db.get_game(conn, 2001)
            g2 = db.get_game(conn, 2002)
            q1 = conn.execute("SELECT quote_text FROM game_quotes WHERE appid=2001").fetchall()
            conn.close()

            self.assertEqual(len(q1), 1, "Duplicate curator quotes should be deduplicated")
            self.assertEqual(g1["is_avoid"], 1)
            self.assertTrue(
                any("简中" in str(r) for r in g1["avoid_reasons_json"]),
                f"Expected schinese-gap avoid reason, got {g1['avoid_reasons_json']}",
            )
            self.assertIsNone(g2, "Explicitly single-player game must be excluded from co-op snapshot")

    def test_build_snapshot_skips_unplayable_coming_soon_and_keeps_demo_and_majority_positive_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            # 3001: coming_soon=True, no demo -> must be skipped
            (fixture / "3001_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 3001,
                        "name": "Unreleased No Demo",
                        "type": "game",
                        "is_free": False,
                        "release_date": {"coming_soon": True},
                        "categories": [{"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )
            # 3002: coming_soon=True, WITH demo -> must be kept
            (fixture / "3002_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 3002,
                        "name": "Unreleased With Demo",
                        "type": "game",
                        "is_free": False,
                        "demos": [{"appid": 30020}],
                        "release_date": {"coming_soon": True},
                        "categories": [{"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )
            # 3003: Valheim-like game (2 positive human curators vs 1 negative, high score) -> must NOT be avoid
            for idx, (fname, rec_cls, desc) in enumerate(
                [
                    ("cur1.html", "color_recommended", "非常棒的四人联机生存游戏"),
                    ("cur2.html", "color_recommended", "和朋友开黑非常好玩"),
                    ("cur3.html", "color_not_recommended", "后期略显赶工"),
                ],
                1,
            ):
                (fixture / fname).write_text(
                    f'<div class="recommendation"><a data-ds-appid="3003" data-ds-tagids="[3843,1662]"><img alt="ValheimLike"></a>'
                    f'<span class="{rec_cls}">评</span><div class="recommendation_desc">{desc}</div></div>',
                    encoding="utf-8",
                )
            (fixture / "3003_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 3003,
                        "name": "ValheimLike",
                        "type": "game",
                        "is_free": False,
                        "price_overview": {"final": 7000, "discount_percent": 0},
                        "release_date": {"coming_soon": False},
                        "categories": [{"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "3003_store.html").write_text(
                '<div data-featuretarget="appreviews" data-props="{&quot;summary_options&quot;:{&quot;summaryGlobalNoOutliers&quot;:{&quot;nReviews&quot;:50000,&quot;fRating&quot;:93.5,&quot;eReviewScore&quot;:8}}}"></div>',
                encoding="utf-8",
            )

            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])
            conn = db.init_db(db_path)
            g_unplayable = db.get_game(conn, 3001)
            g_demo = db.get_game(conn, 3002)
            g_valheim = db.get_game(conn, 3003)
            neg_quotes = conn.execute("SELECT quote_text FROM game_quotes WHERE appid=3003 AND voted_up=0").fetchall()
            conn.close()

            self.assertIsNone(g_unplayable, "Unreleased no-demo game must be excluded from snapshot")
            self.assertIsNotNone(g_demo, "Unreleased game with downloadable Demo must be kept")
            self.assertEqual(g_demo["has_demo"], 1)
            self.assertIsNotNone(g_valheim)
            self.assertEqual(g_valheim["is_avoid"], 0, "High-rated game with 2 positive vs 1 negative curator should not be avoided")
            self.assertEqual(len(neg_quotes), 1, "Negative curator quote should be preserved alongside positive ones")

    def test_resolve_game_parses_remote_steam_search_html(self):
        search_html = """
        <a class="search_result_row" data-ds-appid="1966720" data-ds-tagids="[1667,3843]">
          <span class="title">Lethal Company</span>
        </a>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=search_html)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "empty.sqlite"
            c = db.init_db(db_path)
            c.close()
            fake_client = SteamClient(
                hosts=["https://example.test"],
                cache_dir=str(Path(tmp) / "cache"),
                throttle=0,
                transport=httpx.MockTransport(handler),
            )
            with (
                mock.patch.object(server, "DB_PATH", db_path),
                mock.patch.object(server, "SteamClient", return_value=fake_client),
            ):
                res = server.resolve_game("Lethal Company", limit=5)
                self.assertEqual(res["meta"]["source"], "local+steam")
                self.assertEqual(len(res["items"]), 1)
                self.assertEqual(res["items"][0]["appid"], 1966720)
                self.assertEqual(res["items"][0]["name"], "Lethal Company")


class KookBotBugfixTests(unittest.TestCase):
    def test_intent_parser_negations_and_numeric_budgets(self):
        i1 = kook_bot.parse_intent_from_text("@Bot 我们4个人，不要太恐怖，50以内")
        self.assertEqual(i1["action"], "recommend")
        self.assertEqual(i1["params"]["horror"], "none")
        self.assertEqual(i1["params"]["budget"], "under_50")

        i2 = kook_bot.parse_intent_from_text("@Bot 我们四个人，不想玩恐怖游戏，30以内")
        self.assertEqual(i2["action"], "recommend")
        self.assertEqual(i2["params"]["horror"], "none")
        self.assertEqual(i2["params"]["budget"], "under_50")

        i3 = kook_bot.parse_intent_from_text("@Bot 4人 不要免费的 价格无所谓")
        self.assertEqual(i3["action"], "recommend")
        self.assertNotEqual(i3["params"]["budget"], "free_or_demo")

        i4 = kook_bot.parse_intent_from_text("@Bot 4人 恐怖 100以内")
        self.assertEqual(i4["action"], "recommend")
        self.assertEqual(i4["params"]["horror"], "scary")
        self.assertEqual(i4["params"]["budget"], "any")

        # Real user messages from KOOK screenshots: supplying ANY condition must recommend immediately (no interrogation)
        i5 = kook_bot.parse_intent_from_text("@What to play 两个人玩的微恐游戏")
        self.assertEqual(i5["action"], "recommend")
        self.assertEqual(i5["params"]["players"], 2)
        self.assertEqual(i5["params"]["horror"], "mild")

        i6 = kook_bot.parse_intent_from_text("@What to play 两个人的妖精小游戏")
        self.assertEqual(i6["action"], "recommend")
        self.assertEqual(i6["params"]["players"], 2)
        self.assertEqual(i6["params"]["tag"], "欢乐")

        # Pure greeting / no conditions still asks once
        i_empty = kook_bot.parse_intent_from_text("@What to play 今晚玩啥")
        self.assertEqual(i_empty["action"], "ask")

    def test_new_mention_does_not_leak_stale_pending_filters(self):
        session = kook_bot.KookBotSession(cooldown_sec=0.0)
        # Simulate an abandoned pending dialog that stored budget=free_or_demo
        session.pending_channels["ch-leak"] = {"budget": "free_or_demo"}
        session.pending_since["ch-leak"] = 10**12
        with mock.patch.object(
            server,
            "recommend_coop_games",
            return_value={"status": "ok", "markdown_preview": "**ok**"},
        ) as rec_mock:
            reply = session.handle_message("ch-leak", "(met)999(met) 两个人的妖精小游戏", is_mentioned=True)
            self.assertEqual(reply, "**ok**")
            self.assertEqual(rec_mock.call_args.kwargs["budget"], "any")
            self.assertEqual(rec_mock.call_args.kwargs["players"], 2)
            self.assertEqual(rec_mock.call_args.kwargs["tag"], "欢乐")

    def test_kook_markdown_layout_is_clean_chinese_and_compact(self):
        long_desc = "这是一款超级长的游戏介绍文案，" * 12
        long_quote = "[鉴赏家·UP鉴游组] " + "这是一段非常非常长的鉴赏家百字长评，用来测试排版截断是否干净利落不刷屏。" * 5
        md = server._markdown(
            {
                "8": [
                    {
                        "appid": 4096830,
                        "name": "潜艇菜鸟团",
                        "is_free": False,
                        "has_demo": True,
                        "price_cny": 29.75,
                        "max_players": None,
                        "curator_count": 2,
                        "curators": ["Steam物理互坑·欢乐联机热榜", "UP鉴游组"],
                        "positive_pct": 84.4,
                        "schinese_pct": None,
                        "total_reviews": 120,
                        "short_desc": long_desc,
                        "quotes": [{"quote_text": long_quote, "review_url": "https://store.steampowered.com/app/4096830/"}],
                    }
                ]
            },
            [],
            2,
            "any",
            "free_or_demo",
            mode="online",
            tag="欢乐",
        )
        # No raw English enums, no broken "? 人", no "简中 —", no inline ": > ", no nested "   - " bullets
        self.assertNotIn("free_or_demo", md)
        self.assertNotIn("｜any｜", md)
        self.assertNotIn("支持 ? 人", md)
        self.assertNotIn("简中 —", md)
        self.assertNotIn("：> ", md)
        self.assertNotIn("   - ", md)
        self.assertNotIn("Steam物理互坑·欢乐联机热榜", md)
        self.assertIn("免费/Demo", md)
        self.assertIn("不限恐怖", md)
        # Quote must start on its own line so KOOK renders a proper quote bar
        self.assertTrue(any(line.startswith("> ") for line in md.splitlines()))
        # Compact overall length
        self.assertLess(len(md), 900)

    def test_mild_horror_includes_quirky_horror_and_demo_radar_dedups_fullgame_pair(self):
        conn = db.init_db(sqlite3.connect(":memory:"))
        db.upsert_game(
            conn,
            {
                "appid": 3241660,
                "name": "R.E.P.O.",
                "online_coop": 1,
                "price_cny": 35.0,
                "horror_level": "scary",
                "tags_json": ["恐怖", "喜剧", "欢乐", "在线合作"],
                "review_score": 9,
                "review_tier_zh": "好评如潮",
            },
        )
        db.upsert_game(
            conn,
            {
                "appid": 739630,
                "name": "Pure Hardcore Horror",
                "online_coop": 1,
                "price_cny": 45.0,
                "horror_level": "scary",
                "tags_json": ["恐怖", "生存恐怖", "在线合作"],
                "review_score": 8,
                "review_tier_zh": "特别好评",
            },
        )
        mild_ids = [r["appid"] for r in db.recommend_games(conn, horror="mild", budget="any", players=2)]
        self.assertIn(3241660, mild_ids, "Funny/quirky horror co-op should appear for mild horror")
        self.assertNotIn(739630, mild_ids, "Pure hardcore horror must not appear for mild horror")
        conn.close()

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "dedup.sqlite"
            log_path = Path(tmp) / "log.json"
            c = db.init_db(db_path)
            db.upsert_game(
                c,
                {
                    "appid": 4895870,
                    "name": "浣熊模拟器 Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "fullgame_appid": 2870790,
                    "online_coop": 1,
                    "potential_score": 46.5,
                    "current_players": 352,
                    "followers": 9780,
                },
            )
            db.upsert_game(
                c,
                {
                    "appid": 2870790,
                    "name": "浣熊模拟器",
                    "type": "game",
                    "is_free": 0,
                    "has_demo": 1,
                    "demo_appid": 4895870,
                    "online_coop": 1,
                    "potential_score": 42.0,
                    "current_players": 352,
                    "followers": 9780,
                },
            )
            c.close()
            with mock.patch.multiple(server, DB_PATH=db_path, DEFAULT_DB_PATH=db_path, USER_LOG_PATH=log_path):
                res = server.discover_potential_demos(max_age_days=None, min_score=0.0, limit=5)
                self.assertEqual(len(res["items"]), 1, "Demo and its paired fullgame must not appear twice")

    def test_session_ignores_unsolicited_chat_and_coerces_non_int_llm_players(self):
        session = kook_bot.KookBotSession(cooldown_sec=0.0)
        self.assertIsNone(session.handle_message("ch-1", "吃饭了吗", is_mentioned=False))
        self.assertIsNone(session.handle_message("ch-1", "随便", is_mentioned=False))

        with (
            mock.patch.object(
                kook_bot,
                "call_llm_for_params",
                return_value={"players": "3-4", "horror": "none", "budget": "under_50"},
            ),
            mock.patch.object(
                server,
                "recommend_coop_games",
                return_value={"status": "ok", "markdown_preview": "**ok**"},
            ) as rec_mock,
        ):
            reply = session.handle_message("ch-2", "@Bot 晚上开黑选个游戏", is_mentioned=True)
            self.assertEqual(reply, "**ok**")
            self.assertEqual(rec_mock.call_args.kwargs["players"], 4)

    def test_run_kook_ws_retries_auth_reconnects_on_s5_and_does_not_prewarm_channels(self):
        me_attempts = 0
        ws_connects = 0

        class FakeResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, params=None):
                nonlocal me_attempts
                if url.endswith("/user/me"):
                    me_attempts += 1
                    if me_attempts == 1:
                        raise httpx.ReadTimeout("simulated cross-border timeout")
                    return FakeResp(200, {"code": 0, "data": {"id": "999", "username": "TestBot"}})
                if url.endswith("/guild/list"):
                    return FakeResp(200, {"code": 0, "data": {"items": [{"id": "g1"}]}})
                if url.endswith("/channel/list"):
                    return FakeResp(200, {"code": 0, "data": {"items": [{"id": "c1", "type": 1}]}})
                if url.endswith("/message/list"):
                    return FakeResp(200, {"code": 0, "data": {"items": []}})
                if url.endswith("/gateway/index"):
                    return FakeResp(200, {"code": 0, "data": {"url": "wss://fake.kook/ws"}})
                return FakeResp(200, {"code": 0, "data": {}})

            async def post(self, url, json=None):
                return FakeResp(200, {"code": 0, "data": {}})

        class FakeWS:
            def __init__(self, attempt_idx: int):
                self.attempt_idx = attempt_idx
                self.step = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def send(self, data):
                return None

            async def recv(self):
                self.step += 1
                if self.attempt_idx == 1 and self.step == 1:
                    # KOOK gateway Reconnect signal (s=5)
                    return json.dumps({"s": 5, "d": {"code": 40108, "err": "reconnect"}})
                if self.attempt_idx == 2 and self.step == 1:
                    # Simulate a message that triggers an exception inside handle_message
                    return json.dumps(
                        {
                            "s": 0,
                            "sn": 1,
                            "d": {
                                "type": 9,
                                "channel_type": "GROUP",
                                "target_id": "c1",
                                "msg_id": "m-boom",
                                "author_id": "u1",
                                "content": "@Bot 今晚玩啥",
                                "extra": {"author": {"bot": False}, "mention": ["999"]},
                            },
                        }
                    )
                await asyncio.sleep(0.15)
                raise asyncio.TimeoutError()

        def fake_connect(*args, **kwargs):
            nonlocal ws_connects
            ws_connects += 1
            return FakeWS(ws_connects)

        created_sessions: list[kook_bot.KookBotSession] = []
        orig_session_cls = kook_bot.KookBotSession

        def tracked_session(*args, **kwargs):
            inst = orig_session_cls(*args, **kwargs)
            inst.handle_message = mock.MagicMock(side_effect=RuntimeError("boom in handler"))
            created_sessions.append(inst)
            return inst

        import websockets

        with (
            mock.patch.object(httpx, "AsyncClient", FakeAsyncClient),
            mock.patch.object(websockets, "connect", fake_connect),
            mock.patch.object(kook_bot, "KookBotSession", tracked_session),
            mock.patch.object(kook_bot, "RETRY_BASE_DELAY", 0.01, create=True),
        ):
            asyncio.run(kook_bot.run_kook_ws("fake-token", max_runtime_seconds=0.4))

        self.assertGreaterEqual(me_attempts, 2, "Should retry /user/me after transient timeout")
        self.assertGreaterEqual(ws_connects, 2, "Should reconnect WebSocket after s=5 signal")
        self.assertEqual(len(created_sessions), 1)
        self.assertEqual(
            created_sessions[0].pending_channels,
            {},
            "Startup must NOT pre-warm all text channels into pending_channels",
        )


if __name__ == "__main__":
    unittest.main()
