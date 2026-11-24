"""TDD regression tests for the full-system logic & architecture audit."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coop_night_picker import db, kook_bot, server
from scripts import build_snapshot


class PipelineAuditTests(unittest.TestCase):
    def test_single_player_only_games_are_excluded_even_with_troll_coop_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            (fixture / "curator.html").write_text(
                '<div class="recommendation"><a data-ds-appid="5313770" data-ds-tagids="[3843,4136]"><img alt="RushMath"></a>'
                '<span class="color_recommended">推荐</span><div class="recommendation_desc">单人速算小游戏</div></div>',
                encoding="utf-8",
            )
            (fixture / "5313770_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 5313770,
                        "name": "RushMath",
                        "type": "game",
                        "is_free": False,
                        "price_overview": {"final": 1500, "discount_percent": 0},
                        "release_date": {"coming_soon": False},
                        "categories": [{"id": 2, "description": "单人"}, {"id": 22, "description": "Steam 成就"}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "900001_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 900001,
                        "name": "Real Coop Game",
                        "type": "game",
                        "is_free": True,
                        "release_date": {"coming_soon": False},
                        "categories": [{"id": 2, "description": "单人"}, {"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )

            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])
            conn = db.init_db(db_path)
            solo = db.get_game(conn, 5313770)
            coop = db.get_game(conn, 900001)
            conn.close()
            self.assertIsNone(solo, "Developer-declared single-player-only game must not enter co-op snapshot")
            self.assertIsNotNone(coop)

    def test_troll_horror_tag_beyond_top_8_does_not_corrupt_horror_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            # 12 tags where tag #12 is troll 1721 (心理恐怖), top 8 are pure cozy/coop tags
            tags_payload = [
                {"tagid": tid, "name": f"Tag{i}"}
                for i, tid in enumerate([1685, 3843, 3841, 4136, 19, 21, 1664, 597, 492, 3859, 4508, 1721])
            ]
            (fixture / "2001120_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 2001120,
                        "name": "Split Fiction",
                        "type": "game",
                        "is_free": False,
                        "price_overview": {"final": 19800, "discount_percent": 0},
                        "categories": [{"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "2001120_store.html").write_text(
                f"<script>InitAppTagModal( 2001120, {json.dumps(tags_payload)}, [] );</script>",
                encoding="utf-8",
            )

            # Game with 1721 in top 3 tags -> real mild horror
            mild_tags = [{"tagid": 3843, "name": "在线合作"}, {"tagid": 1721, "name": "心理恐怖"}, {"tagid": 492, "name": "独立"}]
            (fixture / "2001121_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 2001121,
                        "name": "True Mild Horror",
                        "type": "game",
                        "is_free": True,
                        "categories": [{"id": 38, "description": "在线合作"}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "2001121_store.html").write_text(
                f"<script>InitAppTagModal( 2001121, {json.dumps(mild_tags)}, [] );</script>",
                encoding="utf-8",
            )

            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])
            conn = db.init_db(db_path)
            g_clean = db.get_game(conn, 2001120)
            g_mild = db.get_game(conn, 2001121)
            conn.close()
            self.assertEqual(g_clean["horror_level"], "none", "Tag #12 troll 心理恐怖 must not mark game as mild horror")
            self.assertEqual(g_mild["horror_level"], "mild", "Top-8 心理恐怖 tag should mark game as mild horror")

    def test_schinese_avoid_requires_below_70_and_low_score_populates_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            # 1) 92.6% global vs 75.6% schinese -> still Mostly Positive in Chinese, NOT a landmine
            (fixture / "4312190_appdetails.json").write_text(
                json.dumps({"appid": 4312190, "name": "CRACK", "is_free": False, "price_overview": {"final": 3000}, "categories": [{"id": 38}]}),
                encoding="utf-8",
            )
            (fixture / "4312190_store.html").write_text(
                '<div data-featuretarget="appreviews" data-props="{&quot;summary_options&quot;:{&quot;summaryGlobalNoOutliers&quot;:{&quot;nReviews&quot;:2000,&quot;fRating&quot;:92.6,&quot;eReviewScore&quot;:8}},&quot;filter_options&quot;:{&quot;rgLanguageOutliers&quot;:[{&quot;strLanguage&quot;:&quot;schinese&quot;,&quot;nReviews&quot;:200,&quot;fRating&quot;:75.6,&quot;eReviewScore&quot;:6}]}}"></div>',
                encoding="utf-8",
            )
            # 2) Score 5 Mixed (55%) -> is_avoid=1 with explicit percentage reason
            (fixture / "2089980_appdetails.json").write_text(
                json.dumps({"appid": 2089980, "name": "Mixed Game", "is_free": False, "price_overview": {"final": 2000}, "categories": [{"id": 38}]}),
                encoding="utf-8",
            )
            (fixture / "2089980_store.html").write_text(
                '<div data-featuretarget="appreviews" data-props="{&quot;summary_options&quot;:{&quot;summaryGlobalNoOutliers&quot;:{&quot;nReviews&quot;:500,&quot;fRating&quot;:55.2,&quot;eReviewScore&quot;:5}}}"></div>',
                encoding="utf-8",
            )

            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])
            conn = db.init_db(db_path)
            g_crack = db.get_game(conn, 4312190)
            g_mixed = db.get_game(conn, 2089980)
            conn.close()

            self.assertEqual(g_crack["is_avoid"], 0, "75.6% schinese (Mostly Positive) should not be banished to avoid list")
            self.assertEqual(g_mixed["is_avoid"], 1)
            self.assertTrue(any("55%" in str(r) for r in g_mixed["avoid_reasons_json"]), f"Expected explicit pct reason, got {g_mixed['avoid_reasons_json']}")

    def test_build_snapshot_dedups_fullgame_demo_pair_and_circuit_breaks_dead_appreviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            # Unreleased fullgame + its playable Demo in the same snapshot run -> should only keep 1 canonical row
            (fixture / "2870790_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 2870790,
                        "name": "浣熊模拟器",
                        "type": "game",
                        "is_free": False,
                        "release_date": {"coming_soon": True, "date": "即将宣布"},
                        "demos": [{"appid": 4895870}],
                        "categories": [{"id": 38}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "4895870_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 4895870,
                        "name": "浣熊模拟器 Demo",
                        "type": "demo",
                        "is_free": True,
                        "fullgame": {"appid": 2870790},
                        "categories": [{"id": 38}],
                    }
                ),
                encoding="utf-8",
            )
            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])
            conn = db.init_db(db_path)
            count = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1, "Paired Fullgame + Demo must not occupy 2 duplicate rows in snapshot")

    def test_appreviews_circuit_breaker_stops_after_3_consecutive_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "games.sqlite"
            fake_search_html = "".join(
                f'<a class="search_result_row" data-ds-appid="{810000 + i}" data-ds-tagids="[3843]">'
                f'<span class="title">Coop {i}</span></a>'
                for i in range(5)
            )

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    self.appreviews_calls = 0

                def store_search(self, query, **params):
                    return fake_search_html

                def curator_recommendations(self, *args, **kwargs):
                    return {"success": 1, "results_html": ""}

                def appdetails(self, appid):
                    return {
                        str(appid): {
                            "success": True,
                            "data": {
                                "steam_appid": appid,
                                "name": f"Coop {appid}",
                                "type": "game",
                                "is_free": True,
                                "release_date": {"coming_soon": False},
                                "categories": [{"id": 38, "description": "在线合作"}],
                            },
                        }
                    }

                def store_page(self, appid):
                    return ""

                def current_players(self, appid):
                    return 0

                def community_followers(self, appid):
                    return 0

                def app_news(self, appid, count=5):
                    return []

                def appreviews(self, appid, review_type=None, playtime_filter_min=1):
                    self.appreviews_calls += 1
                    return {"ok": False, "unavailable": True}

            fake_client = FakeClient()
            with mock.patch.object(build_snapshot, "SteamClient", return_value=fake_client):
                build_snapshot.main(["--limit", "10", "--db", str(db_path), "--with-reviews"])

            self.assertEqual(
                fake_client.appreviews_calls,
                3,
                f"Circuit breaker must disable appreviews after 3 consecutive unavailable responses, got {fake_client.appreviews_calls}",
            )

    def test_ranking_prefers_higher_positive_pct_within_same_tier(self):
        conn = db.init_db(sqlite3.connect(":memory:"))
        db.upsert_game(
            conn,
            {
                "appid": 1260320,
                "name": "Party Animals",
                "online_coop": 1,
                "price_cny": 98.0,
                "review_score": 8,
                "positive_pct": 81.4,
                "curator_count": 4,
                "curators_json": ["Steam物理互坑·欢乐联机热榜", "Steam社交派对·欢乐联机热榜", "UP鉴游组", "小黑盒-盒娘后援会"],
                "tags_json": ["欢乐", "派对游戏"],
            },
        )
        db.upsert_game(
            conn,
            {
                "appid": 4069520,
                "name": "Super Golf",
                "online_coop": 1,
                "price_cny": 48.0,
                "review_score": 8,
                "positive_pct": 93.8,
                "curator_count": 3,
                "curators_json": ["Steam物理互坑·欢乐联机热榜", "Steam社交派对·欢乐联机热榜", "中国鉴赏家同好会"],
                "tags_json": ["欢乐", "派对游戏"],
            },
        )
        rows = db.recommend_games(conn, horror="any", budget="any", players=4)
        conn.close()
        self.assertEqual(rows[0]["appid"], 4069520, "93.8% game should rank ahead of 81.4% game within Score 8 tier")


class KookAndServerAuditTests(unittest.TestCase):
    def test_unmentioned_casual_chat_does_not_false_trigger_and_spaced_mention_works(self):
        session = kook_bot.KookBotSession(cooldown_sec=0.0)
        for casual in (
            "在吗 上号",
            "说话啊麦炸了",
            "我买了个耳机推荐给你",
            "这把避雷这个英雄",
            "这个新人很有潜力",
            "我玩过英雄联盟",
            "发一下活动详情",
        ):
            self.assertIsNone(
                session.handle_message("ch-casual", casual, is_mentioned=False),
                f"Casual message must NOT false-trigger bot: {casual!r}",
            )

        # Spaced display-name mention "@What to play 搜 致命公司" must parse as search
        intent = kook_bot.parse_intent_from_text("@What to play 搜 致命公司")
        self.assertEqual(intent["action"], "search")
        self.assertEqual(intent["query"], "致命公司")

        # Pure greeting "@What to play 今晚玩啥" must NOT waste 1-10s calling LLM
        with mock.patch.object(kook_bot, "call_llm_for_params") as llm_mock:
            reply = session.handle_message("ch-greet", "@What to play 今晚玩啥", is_mentioned=True)
            self.assertIn("开黑选游助手", reply)
            llm_mock.assert_not_called()

        # Hallucinated LLM enum values must be sanitized safely
        with (
            mock.patch.object(
                kook_bot,
                "call_llm_for_params",
                return_value={"horror": "super_duper", "budget": "cheap_ish", "players": "4人", "mode": "coop"},
            ),
            mock.patch.object(
                server,
                "recommend_coop_games",
                return_value={"status": "ok", "markdown_preview": "**ok**"},
            ) as rec_mock,
        ):
            reply = session.handle_message("ch-llm", "@What to play 想找个轻松下班玩一局的", is_mentioned=True)
            self.assertEqual(reply, "**ok**")
            self.assertIn(rec_mock.call_args.kwargs["horror"], {"none", "mild", "scary", "any"})
            self.assertIn(rec_mock.call_args.kwargs["budget"], {"free_or_demo", "under_50", "any_paid", "any"})
            self.assertIn(rec_mock.call_args.kwargs["mode"], {"online", "local", "any"})

    def test_kook_card_formatting_rounds_percentages_and_hides_steam_feeds(self):
        session = kook_bot.KookBotSession(cooldown_sec=0.0)
        fake_card = {
            "status": "ok",
            "appid": 3241660,
            "name": "R.E.P.O.",
            "stars": "★★★★★",
            "review_tier": "好评如潮",
            "positive_pct": 96.05520885528173,
            "schinese_pct": None,
            "is_free": False,
            "price_cny": 35.0,
            "has_demo": False,
            "max_players": None,
            "curator_count": 3,
            "curators": ["Steam物理互坑·欢乐联机热榜", "游饼"],
            "positive_quotes": [{"quote_text": "[鉴赏家·游饼] 爆笑多人游戏"}],
        }
        with mock.patch.object(server, "get_game_card", return_value=fake_card):
            reply = session.handle_message("ch-card", "@What to play 详情 3241660", is_mentioned=True)
            self.assertIn("96.1%", reply)
            self.assertNotIn("96.055", reply)
            self.assertNotIn("简中 —", reply)
            self.assertNotIn("?人", reply)
            self.assertNotIn("Steam物理互坑", reply)
            self.assertIn("游饼", reply)
            self.assertIn("https://store.steampowered.com/app/3241660/", reply)

    def test_kook_dm_event_replies_to_author_id_not_bot_target_id(self):
        posted: list[tuple[str, dict]] = []

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
                if url.endswith("/user/me"):
                    return FakeResp(200, {"code": 0, "data": {"id": "999", "username": "What to play"}})
                if url.endswith("/guild/list"):
                    return FakeResp(200, {"code": 0, "data": {"items": []}})
                if url.endswith("/gateway/index"):
                    return FakeResp(200, {"code": 0, "data": {"url": "wss://fake.kook/ws"}})
                return FakeResp(200, {"code": 0, "data": {}})

            async def post(self, url, json=None):
                posted.append((url, dict(json or {})))
                return FakeResp(200, {"code": 0, "data": {}})

        class FakeWS:
            def __init__(self):
                self.sent_dm = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def send(self, data):
                return None

            async def recv(self):
                if not self.sent_dm:
                    self.sent_dm = True
                    # In KOOK PERSON (DM) events, target_id is the bot ID (999) and author_id is the user (user-42)
                    return json.dumps(
                        {
                            "s": 0,
                            "sn": 1,
                            "d": {
                                "type": 9,
                                "channel_type": "PERSON",
                                "target_id": "999",
                                "msg_id": "dm-1",
                                "author_id": "user-42",
                                "content": "今晚玩啥",
                                "extra": {"author": {"bot": False}},
                            },
                        }
                    )
                await asyncio.sleep(0.15)
                raise asyncio.TimeoutError()

        import websockets

        with (
            mock.patch.object(httpx, "AsyncClient", FakeAsyncClient),
            mock.patch.object(websockets, "connect", lambda *a, **k: FakeWS()),
            mock.patch.object(kook_bot, "RETRY_BASE_DELAY", 0.01),
        ):
            asyncio.run(kook_bot.run_kook_ws("fake-token", max_runtime_seconds=0.3))

        self.assertEqual(len(posted), 1)
        self.assertTrue(posted[0][0].endswith("/direct-message/create"))
        self.assertEqual(posted[0][1]["target_id"], "user-42", "DM reply must go to author_id, never bot target_id")

    def test_server_hot_reloads_newer_cloud_snapshot_via_etag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_db = root / "local.sqlite"
            cloud_cache = root / "cloud.sqlite"
            c = db.init_db(local_db)
            db.upsert_game(c, {"appid": 1, "name": "Old Local", "online_coop": 1, "is_free": 1, "review_score": 8})
            c.close()

            # Build a newer cloud DB payload in memory/file
            remote_src = root / "remote_src.sqlite"
            c2 = db.init_db(remote_src)
            db.upsert_game(c2, {"appid": 2, "name": "Fresh Cloud", "online_coop": 1, "is_free": 1, "review_score": 9})
            c2.close()
            remote_bytes = remote_src.read_bytes()

            # Make local_db older
            old_ts = time.time() - 3600
            os.utime(local_db, (old_ts, old_ts))

            seen_etags: list[str | None] = []

            class FakeURLResp:
                def __init__(self, status: int, data: bytes, etag: str):
                    self.status = status
                    self._data = data
                    self.headers = {"ETag": etag}

                def read(self):
                    return self._data

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            def fake_urlopen(req, timeout=8):
                etag = req.headers.get("If-none-match")
                seen_etags.append(etag)
                if etag == '"v2"':
                    import urllib.error

                    raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
                return FakeURLResp(200, remote_bytes, '"v2"')

            with (
                mock.patch.multiple(
                    server,
                    DB_PATH=local_db,
                    DEFAULT_DB_PATH=local_db,
                    CLOUD_CACHE_PATH=cloud_cache,
                    _CLOUD_ETAG=None,
                ),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                self.assertTrue(server.refresh_cloud_snapshot(force=True))
                self.assertFalse(server.refresh_cloud_snapshot(force=True))
                self.assertEqual(seen_etags, [None, '"v2"'])
                conn = server._open_snapshot()
                g = db.get_game(conn, 2)
                conn.close()
                self.assertIsNotNone(g, "_open_snapshot should prefer freshly downloaded newer cloud snapshot")


if __name__ == "__main__":
    unittest.main()
