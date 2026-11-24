"""TDD tests for Early Potential Demo Radar (小而美 / 1-7天新Demo潜力发现)."""
from __future__ import annotations

import datetime
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coop_night_picker import constants, db, extractor, kook_bot, server
from coop_night_picker.steam_client import SteamClient
from scripts import build_snapshot


class DemoRadarClientAndExtractorTests(unittest.TestCase):
    def test_constants_define_demo_radar_feeds_and_publisher_whitelist(self):
        self.assertTrue(hasattr(constants, "DEMO_RADAR_FEEDS"))
        self.assertTrue(any(f.get("category1") == 10 and f.get("sort_by") == "Released_DESC" for f in constants.DEMO_RADAR_FEEDS))
        self.assertTrue(hasattr(constants, "INDIE_PUBLISHER_WHITELIST"))
        self.assertIn("devolver digital", constants.INDIE_PUBLISHER_WHITELIST)

    def test_steam_client_early_signal_endpoints(self):
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            if "GetNumberOfCurrentPlayers" in request.url.path:
                return httpx.Response(200, json={"response": {"player_count": 28, "result": 1}})
            if "memberslistxml" in request.url.path:
                return httpx.Response(200, text="<memberList><memberCount>307</memberCount></memberList>")
            if "GetNewsForApp" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "appnews": {
                            "appid": 5523130,
                            "newsitems": [
                                {"gid": "1", "title": "Hotfix 1.0.1", "date": 1795400000},
                                {"gid": "2", "title": "Demo Launch!", "date": 1795300000},
                            ],
                        }
                    },
                )
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            client = SteamClient(
                cache_dir=tmp,
                throttle=0,
                retries=0,
                transport=httpx.MockTransport(handler),
            )
            self.assertEqual(client.current_players(5522330), 28)
            self.assertEqual(client.community_followers(5306870), 307)
            news = client.app_news(5523130, count=5)
            self.assertEqual(len(news), 2)
            self.assertEqual(news[0]["title"], "Hotfix 1.0.1")
            self.assertTrue(any("api.steampowered.com" in u and "5522330" in u for u in seen_urls))
            self.assertTrue(any("steamcommunity.com/games/5306870/memberslistxml" in u for u in seen_urls))

    def test_extractor_parses_release_age_appdetails_polish_and_ai_disclosure(self):
        today = datetime.date(2026, 11, 24)
        self.assertEqual(extractor.parse_release_age_days("2026 年 11 月 23 日", today=today), 1)
        self.assertEqual(extractor.parse_release_age_days("Nov 20, 2026", today=today), 4)
        self.assertEqual(extractor.parse_release_age_days("20 Nov, 2026", today=today), 4)
        self.assertEqual(extractor.parse_release_age_days("2026-11-22", today=today), 2)
        self.assertIsNone(extractor.parse_release_age_days("即将宣布", today=today))

        search_html = """
        <a class="search_result_row" data-ds-appid="5522330" data-ds-tagids="[3843,492]">
          <span class="title">螈宝：桌面环游 Demo</span>
          <div class="col search_released responsive_secondrow">2026 年 11 月 23 日</div>
        </a>
        """
        rows = extractor.parse_search_html(search_html)
        self.assertEqual(rows[0]["release_date_text"], "2026 年 11 月 23 日")

        details = extractor.parse_appdetails(
            {
                "5522330": {
                    "success": True,
                    "data": {
                        "steam_appid": 5522330,
                        "name": "螈宝：桌面环游 Demo",
                        "type": "demo",
                        "is_free": True,
                        "release_date": {"coming_soon": False, "date": "2026 年 11 月 23 日"},
                        "supported_languages": "英语, 简体中文<strong>*</strong>",
                        "movies": [{"id": 1, "name": "Trailer"}],
                        "screenshots": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}],
                        "developers": ["Axolotl Go"],
                        "publishers": ["Gamera Games"],
                        "fullgame": {"appid": "5318180", "name": "螈宝：桌面环游"},
                        "categories": [{"id": 38, "description": "在线合作"}],
                    },
                }
            },
            5522330,
        )
        self.assertEqual(details["release_date"], "2026 年 11 月 23 日")
        self.assertEqual(details["has_schinese"], 1)
        self.assertEqual(details["has_trailer"], 1)
        self.assertEqual(details["screenshot_count"], 4)
        self.assertEqual(details["developers_json"], ["Axolotl Go"])
        self.assertEqual(details["publishers_json"], ["Gamera Games"])
        self.assertEqual(details["fullgame_appid"], 5318180)

        ai_page = extractor.parse_store_page("<div><h2>AI 生成内容披露</h2><p>Uses AI assets</p></div>")
        clean_page = extractor.parse_store_page("<div><p>Hand-drawn indie co-op game</p></div>")
        self.assertEqual(ai_page["has_ai_disclosure"], 1)
        self.assertEqual(clean_page["has_ai_disclosure"], 0)

    def test_compute_potential_score_ranks_real_traction_above_zero_noise(self):
        psst = extractor.compute_potential_score(
            current_players=21, followers=307, age_days=1, recent_news_count=0, has_schinese=0, has_trailer=0
        )
        axolotl = extractor.compute_potential_score(
            current_players=28, followers=5, age_days=1, recent_news_count=0, has_schinese=1, has_trailer=1
        )
        purloin = extractor.compute_potential_score(
            current_players=0, followers=5, age_days=1, recent_news_count=3, has_schinese=1, has_trailer=0
        )
        sus_noise = extractor.compute_potential_score(
            current_players=0, followers=6, age_days=1, recent_news_count=0, has_schinese=1, has_trailer=0
        )
        ai_penalized = extractor.compute_potential_score(
            current_players=28, followers=5, age_days=1, recent_news_count=0, has_schinese=1, has_trailer=1, has_ai_disclosure=1
        )
        pub_boosted = extractor.compute_potential_score(
            current_players=28,
            followers=5,
            age_days=1,
            recent_news_count=0,
            has_schinese=1,
            has_trailer=1,
            publishers=["Devolver Digital"],
        )

        self.assertGreater(psst, axolotl)
        self.assertGreater(axolotl, purloin)
        self.assertGreater(purloin, sus_noise)
        self.assertLessEqual(sus_noise, 3.0, "Zero-traction noise Demo should be capped low")
        self.assertLess(ai_penalized, axolotl)
        self.assertGreater(pub_boosted, axolotl)


class DemoRadarDbServerAndKookTests(unittest.TestCase):
    def test_db_migration_and_recommend_potential_demos(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite"
            raw = sqlite3.connect(db_path)
            raw.execute(
                "CREATE TABLE games (appid INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT DEFAULT 'game', "
                "is_free INTEGER DEFAULT 0, has_demo INTEGER DEFAULT 0, demo_appid INTEGER, fullgame_appid INTEGER, "
                "price_cny REAL DEFAULT 0.0, discount_pct INTEGER DEFAULT 0, online_coop INTEGER DEFAULT 0, "
                "local_coop INTEGER DEFAULT 0, remote_play INTEGER DEFAULT 0, max_players INTEGER, "
                "horror_level TEXT DEFAULT 'none', review_score INTEGER DEFAULT 0, review_tier_zh TEXT DEFAULT '评测不足', "
                "total_reviews INTEGER DEFAULT 0, positive_pct REAL DEFAULT 0.0, schinese_score INTEGER, "
                "schinese_pct REAL, schinese_total INTEGER, short_desc TEXT, categories_json TEXT, "
                "tags_json TEXT, avoid_reasons_json TEXT, is_avoid INTEGER DEFAULT 0, header_image TEXT, "
                "updated_at TEXT, curator_count INTEGER DEFAULT 0, curators_json TEXT, coming_soon INTEGER DEFAULT 0)"
            )
            raw.commit()
            raw.close()

            conn = db.init_db(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
            for col in (
                "release_date",
                "release_age_days",
                "current_players",
                "followers",
                "recent_news_count",
                "has_schinese",
                "has_trailer",
                "has_ai_disclosure",
                "developers_json",
                "publishers_json",
                "potential_score",
                "is_demo_radar",
            ):
                self.assertIn(col, cols)

            # High-potential 1-day demo
            db.upsert_game(
                conn,
                {
                    "appid": 5342980,
                    "name": "Psst! Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "online_coop": 1,
                    "horror_level": "none",
                    "release_date": "2026 年 11 月 23 日",
                    "release_age_days": 1,
                    "current_players": 21,
                    "followers": 307,
                    "potential_score": 23.7,
                    "is_demo_radar": 1,
                },
            )
            # High-potential 2-day demo
            db.upsert_game(
                conn,
                {
                    "appid": 5522330,
                    "name": "螈宝：桌面环游 Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "online_coop": 1,
                    "horror_level": "none",
                    "release_date": "2026 年 11 月 22 日",
                    "release_age_days": 2,
                    "current_players": 28,
                    "followers": 5,
                    "has_schinese": 1,
                    "has_trailer": 1,
                    "potential_score": 19.7,
                    "is_demo_radar": 1,
                },
            )
            # Zero-traction noise demo (score below threshold)
            db.upsert_game(
                conn,
                {
                    "appid": 5646510,
                    "name": "SUS! Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "online_coop": 1,
                    "horror_level": "none",
                    "release_age_days": 1,
                    "current_players": 0,
                    "followers": 6,
                    "potential_score": 2.0,
                    "is_demo_radar": 1,
                },
            )
            # Old 120-day demo (outside max_age_days=14)
            db.upsert_game(
                conn,
                {
                    "appid": 999001,
                    "name": "Old Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "online_coop": 1,
                    "horror_level": "none",
                    "release_age_days": 120,
                    "current_players": 50,
                    "potential_score": 18.0,
                    "is_demo_radar": 1,
                },
            )
            # Unplayable coming-soon game without demo
            db.upsert_game(
                conn,
                {
                    "appid": 999002,
                    "name": "Unplayable Wishlist",
                    "type": "game",
                    "is_free": 0,
                    "has_demo": 0,
                    "coming_soon": 1,
                    "online_coop": 1,
                    "horror_level": "none",
                    "potential_score": 25.0,
                },
            )

            rows = db.recommend_potential_demos(conn, max_age_days=14, min_score=8.0, limit=10)
            self.assertEqual([r["appid"] for r in rows], [5342980, 5522330])
            conn.close()

    def test_build_snapshot_persists_demo_radar_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            (fixture / "5522330_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 5522330,
                        "name": "螈宝：桌面环游 Demo",
                        "type": "demo",
                        "is_free": True,
                        "release_date": {"coming_soon": False, "date": "2026 年 11 月 23 日"},
                        "supported_languages": "简体中文, 英语",
                        "movies": [{"id": 1}],
                        "developers": ["Axolotl Go"],
                        "publishers": ["Axolotl Go"],
                        "fullgame": {"appid": 5318180},
                        "categories": [{"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "5522330_early.json").write_text(
                json.dumps({"current_players": 28, "followers": 120, "recent_news_count": 2}),
                encoding="utf-8",
            )

            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])
            conn = db.init_db(db_path)
            g = db.get_game(conn, 5522330)
            conn.close()

            self.assertIsNotNone(g)
            self.assertEqual(g["current_players"], 28)
            self.assertEqual(g["followers"], 120)
            self.assertEqual(g["recent_news_count"], 2)
            self.assertEqual(g["has_schinese"], 1)
            self.assertEqual(g["has_trailer"], 1)
            self.assertGreater(g["potential_score"], 15.0)
            self.assertEqual(g["is_demo_radar"], 1)

    def test_server_discover_potential_demos_and_kook_bot_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "games.sqlite"
            log_path = Path(tmp) / "user_log.json"
            conn = db.init_db(db_path)
            db.upsert_game(
                conn,
                {
                    "appid": 5342980,
                    "name": "Psst! Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "online_coop": 1,
                    "horror_level": "none",
                    "release_date": "2026 年 11 月 23 日",
                    "release_age_days": 1,
                    "current_players": 21,
                    "followers": 307,
                    "recent_news_count": 2,
                    "has_schinese": 1,
                    "has_trailer": 1,
                    "short_desc": "快节奏多人潜行整活试玩版。",
                    "potential_score": 23.7,
                    "is_demo_radar": 1,
                },
            )
            conn.close()

            with mock.patch.multiple(server, DB_PATH=db_path, DEFAULT_DB_PATH=db_path, USER_LOG_PATH=log_path):
                res = server.discover_potential_demos(max_age_days=14, limit=5)
                self.assertEqual(res["status"], "ok")
                self.assertEqual(len(res["items"]), 1)
                md = res["markdown_preview"]
                self.assertIn("潜力新 Demo 雷达", md)
                self.assertIn("Psst! Demo", md)
                self.assertIn("21", md)
                self.assertIn("307", md)

                # KOOK intent parsing & session direct dispatch
                i1 = kook_bot.parse_intent_from_text("@Bot 潜力demo")
                self.assertEqual(i1["action"], "potential_demos")

                i2 = kook_bot.parse_intent_from_text("@Bot 有什么小而美的新demo")
                self.assertEqual(i2["action"], "potential_demos")

                i3 = kook_bot.parse_intent_from_text("@Bot 4人 不要恐怖 潜力新游")
                self.assertEqual(i3["action"], "potential_demos")
                self.assertEqual(i3["params"]["horror"], "none")
                self.assertEqual(i3["params"]["players"], 4)

                sess = kook_bot.KookBotSession(cooldown_sec=0.0)
                reply = sess.handle_message("chan-radar", "有没有刚出的小而美潜力demo", is_mentioned=False)
                self.assertIsNotNone(reply)
                self.assertIn("潜力新 Demo 雷达", reply)
                self.assertIn("Psst! Demo", reply)


if __name__ == "__main__":
    unittest.main()
