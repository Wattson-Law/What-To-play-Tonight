"""TDD tests for P0 features:
1. Cloud-persistent play/dislike log (GitHub bot-state branch + scope isolation + 409 merge + undo).
2. KOOK natural-language feedback loop ("第2个不好玩推荐点别的", "浣熊模拟器玩过了", "这些都不好玩换一批", "换一批", "撤销", "已记录").
3. Demo Radar velocity tracking (24h delta_ccu / delta_followers), 429 retry/carry-forward, and old-AAA filtering.
4. Build-time max_players deep extraction (about_the_game/detailed_description) and incremental short_desc_zh enrichment.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coop_night_picker import db, extractor, kook_bot, server
from scripts import build_snapshot


class CloudStateAndFeedbackLoopTests(unittest.TestCase):
    def test_cloud_state_store_pulls_pushes_and_merges_on_409(self):
        from coop_night_picker.state_store import GitHubStateStore

        remote_file: dict[str, str] = {}
        remote_sha = "sha-v1"
        remote_branch_exists = False
        put_attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal remote_sha, remote_branch_exists, put_attempts
            url = str(request.url)
            if request.method == "GET" and "/contents/state/user_log.json" in url:
                if not remote_file:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(
                    200,
                    json={
                        "sha": remote_sha,
                        "content": base64.b64encode(remote_file["body"].encode("utf-8")).decode("ascii"),
                        "encoding": "base64",
                    },
                )
            if request.method == "GET" and "/git/ref/heads/bot-state" in url:
                if not remote_branch_exists:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"ref": "refs/heads/bot-state", "object": {"sha": "commit-1"}})
            if request.method == "POST" and "/git/blobs" in url:
                return httpx.Response(201, json={"sha": "blob-init"})
            if request.method == "POST" and "/git/trees" in url:
                return httpx.Response(201, json={"sha": "tree-init"})
            if request.method == "POST" and "/git/commits" in url:
                return httpx.Response(201, json={"sha": "commit-init"})
            if request.method == "POST" and "/git/refs" in url:
                remote_branch_exists = True
                return httpx.Response(201, json={"ref": "refs/heads/bot-state", "object": {"sha": "commit-init"}})
            if request.method == "PUT" and "/contents/state/user_log.json" in url:
                put_attempts += 1
                payload = json.loads(request.content.decode("utf-8"))
                decoded = base64.b64decode(payload["content"]).decode("utf-8")
                # Simulate a 409 conflict on the second push where another runner saved appid 200
                if put_attempts == 2:
                    remote_sha = "sha-v2-concurrent"
                    remote_file["body"] = json.dumps(
                        {
                            "version": 2,
                            "scopes": {
                                "guild:g1": {
                                    "records": [
                                        {"appid": 200, "status": "played", "name": "OtherGame", "updated_at": 9999}
                                    ]
                                }
                            },
                        }
                    )
                    return httpx.Response(409, json={"message": "SHA does not match"})
                remote_file["body"] = decoded
                remote_sha = f"sha-v{put_attempts + 1}"
                return httpx.Response(200, json={"content": {"sha": remote_sha}})
            return httpx.Response(404, json={"message": f"Unhandled {request.method} {url}"})

        store = GitHubStateStore(
            repo="Wattson-Law/What-To-play-Tonight",
            token="ghs_test_token",
            branch="bot-state",
            path="state/user_log.json",
            transport=httpx.MockTransport(handler),
        )
        # First push bootstraps orphan branch & writes initial state
        state1 = {
            "version": 2,
            "scopes": {
                "guild:g1": {"records": [{"appid": 100, "status": "avoided", "name": "BadGame", "updated_at": 1000}]}
            },
        }
        self.assertTrue(store.push(state1))
        self.assertTrue(remote_branch_exists)

        # Second push triggers 409, merges concurrent appid 200 + local appid 300, and succeeds
        state2 = {
            "version": 2,
            "scopes": {
                "guild:g1": {
                    "records": [
                        {"appid": 100, "status": "avoided", "name": "BadGame", "updated_at": 1000},
                        {"appid": 300, "status": "played", "name": "FunGame", "updated_at": 2000},
                    ]
                }
            },
        }
        self.assertTrue(store.push(state2))
        pulled = store.pull()
        self.assertIsNotNone(pulled)
        g1_ids = {r["appid"] for r in pulled["scopes"]["guild:g1"]["records"]}
        self.assertEqual(g1_ids, {100, 200, 300})

    def test_server_scope_isolation_unlog_and_list_play_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "user_log.json"
            db_path = Path(tmp) / "games.sqlite"
            conn = db.init_db(db_path)
            for aid, name in ((101, "Alpha Coop"), (102, "Beta Coop"), (103, "Gamma Coop")):
                db.upsert_game(
                    conn,
                    {
                        "appid": aid,
                        "name": name,
                        "online_coop": 1,
                        "is_free": 1,
                        "review_score": 8,
                        "review_tier_zh": "特别好评",
                        "positive_pct": 92.0,
                        "curator_count": 1,
                        "curators_json": ["UP鉴游组"],
                    },
                )
            conn.close()

            with (
                mock.patch.object(server, "DB_PATH", db_path),
                mock.patch.object(server, "DEFAULT_DB_PATH", db_path),
                mock.patch.object(server, "USER_LOG_PATH", log_path),
                mock.patch.dict("os.environ", {"COOP_NIGHT_PICKER_DISABLE_CLOUD": "1"}),
            ):
                # Scope A marks 101 as avoided ("不好玩")
                server.log_play(101, "avoided", note="不好玩", scope="guild:A", name="Alpha Coop")
                # Scope A must exclude 101; Scope B must STILL see 101!
                rec_a = server.recommend_coop_games(horror="any", budget="any", players=4, confirmed=True, scope="guild:A")
                rec_b = server.recommend_coop_games(horror="any", budget="any", players=4, confirmed=True, scope="guild:B")
                ids_a = [g["appid"] for g in rec_a["recommended"]]
                ids_b = [g["appid"] for g in rec_b["recommended"]]
                self.assertNotIn(101, ids_a)
                self.assertIn(101, ids_b)

                # Temporary extra_excluded skips 102 without persisting it
                rec_a2 = server.recommend_coop_games(
                    horror="any", budget="any", players=4, confirmed=True, scope="guild:A", extra_excluded={102}
                )
                self.assertEqual([g["appid"] for g in rec_a2["recommended"]], [103])

                # List & unlog restores 101
                listing = server.list_play_log(scope="guild:A")
                self.assertEqual(len(listing["records"]), 1)
                self.assertEqual(listing["records"][0]["name"], "Alpha Coop")

                undo = server.unlog_play(101, scope="guild:A")
                self.assertEqual(undo["status"], "removed")
                rec_a3 = server.recommend_coop_games(horror="any", budget="any", players=4, confirmed=True, scope="guild:A")
                self.assertIn(101, [g["appid"] for g in rec_a3["recommended"]])

    def test_kook_feedback_loop_dislike_by_index_or_name_auto_rerolls_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "user_log.json"
            db_path = Path(tmp) / "games.sqlite"
            conn = db.init_db(db_path)
            games = [
                (1001, "浣熊模拟器", 96.0),
                (1002, "R.E.P.O.", 95.0),
                (1003, "致命公司", 94.0),
                (1004, "猛兽派对", 93.0),
                (1005, "超鸡马", 92.0),
                (1006, "胡闹厨房2", 91.0),
            ]
            for aid, name, pct in games:
                db.upsert_game(
                    conn,
                    {
                        "appid": aid,
                        "name": name,
                        "online_coop": 1,
                        "is_free": 1,
                        "review_score": 8,
                        "review_tier_zh": "特别好评",
                        "positive_pct": pct,
                        "curator_count": 2,
                        "curators_json": ["UP鉴游组", "游饼"],
                    },
                )
            conn.close()

            with (
                mock.patch.object(server, "DB_PATH", db_path),
                mock.patch.object(server, "DEFAULT_DB_PATH", db_path),
                mock.patch.object(server, "USER_LOG_PATH", log_path),
                mock.patch.dict("os.environ", {"COOP_NIGHT_PICKER_DISABLE_CLOUD": "1"}),
            ):
                session = kook_bot.KookBotSession(cooldown_sec=0.0)
                # 1. Ask for recommendations -> gets top 3 (1001, 1002, 1003)
                r1 = session.handle_message("ch-1", "@What to play 4人联机", is_mentioned=True, scope="guild:g1")
                self.assertIn("浣熊模拟器", r1)
                self.assertIn("R.E.P.O.", r1)
                self.assertIn("致命公司", r1)

                # 2. User says "第2个不好玩，推荐点别的" (even WITHOUT @mention right after recommendation!)
                r2 = session.handle_message("ch-1", "第2个不好玩，推荐点别的", is_mentioned=False, scope="guild:g1")
                self.assertIsNotNone(r2)
                self.assertIn("R.E.P.O.", r2)  # confirms what was recorded
                self.assertIn("猛兽派对", r2)  # freshly re-recommended replacement!
                # Verify persisted as avoided in guild:g1
                saved = server.list_play_log(scope="guild:g1")["records"]
                self.assertTrue(any(x["appid"] == 1002 and x["status"] == "avoided" for x in saved))

                # 3. User says "@What to play 浣熊模拟器玩过了，换一批"
                r3 = session.handle_message("ch-1", "@What to play 浣熊模拟器玩过了，换一批", is_mentioned=True, scope="guild:g1")
                self.assertIn("浣熊模拟器", r3)
                self.assertIn("超鸡马", r3)
                saved = server.list_play_log(scope="guild:g1")["records"]
                self.assertTrue(any(x["appid"] == 1001 and x["status"] == "played" for x in saved))

                # 4. User says "已记录" to inspect cloud-persisted exclusions
                r4 = session.handle_message("ch-1", "@What to play 已记录", is_mentioned=True, scope="guild:g1")
                self.assertIn("浣熊模拟器", r4)
                self.assertIn("R.E.P.O.", r4)

                # 5. User says "撤销" -> undoes the last recorded item (1001)
                r5 = session.handle_message("ch-1", "@What to play 撤销", is_mentioned=True, scope="guild:g1")
                self.assertIn("已撤销", r5)
                saved = server.list_play_log(scope="guild:g1")["records"]
                self.assertFalse(any(x["appid"] == 1001 for x in saved))

    def test_kook_batch_dislike_and_neutral_reroll(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "user_log.json"
            db_path = Path(tmp) / "games.sqlite"
            conn = db.init_db(db_path)
            for idx in range(1, 8):
                db.upsert_game(
                    conn,
                    {
                        "appid": 2000 + idx,
                        "name": f"CoopGame{idx}",
                        "online_coop": 1,
                        "is_free": 1,
                        "review_score": 8,
                        "review_tier_zh": "特别好评",
                        "positive_pct": 98.0 - idx,
                        "curator_count": 1,
                        "curators_json": ["UP鉴游组"],
                    },
                )
            conn.close()

            with (
                mock.patch.object(server, "DB_PATH", db_path),
                mock.patch.object(server, "DEFAULT_DB_PATH", db_path),
                mock.patch.object(server, "USER_LOG_PATH", log_path),
                mock.patch.dict("os.environ", {"COOP_NIGHT_PICKER_DISABLE_CLOUD": "1"}),
            ):
                session = kook_bot.KookBotSession(cooldown_sec=0.0)
                r1 = session.handle_message("ch-2", "@What to play 4人免费", is_mentioned=True, scope="guild:g2")
                self.assertIn("CoopGame1", r1)

                # Neutral "换一批" skips CoopGame1..3 temporarily WITHOUT permanently recording avoided
                r2 = session.handle_message("ch-2", "@What to play 换一批", is_mentioned=True, scope="guild:g2")
                self.assertIn("CoopGame4", r2)
                self.assertNotIn("CoopGame1", r2)
                self.assertEqual(server.list_play_log(scope="guild:g2")["records"], [])

                # Batch dislike "这几个不好玩，推荐点别的" permanently excludes CoopGame4..6 and shows CoopGame7
                r3 = session.handle_message("ch-2", "这几个不好玩，推荐点别的", is_mentioned=False, scope="guild:g2")
                self.assertIn("CoopGame7", r3)
                saved_ids = {r["appid"] for r in server.list_play_log(scope="guild:g2")["records"]}
                self.assertEqual(saved_ids, {2004, 2005, 2006})


class DemoRadarVelocityAndEnrichmentTests(unittest.TestCase):
    def test_deep_max_players_extraction_from_about_the_game_and_english_patterns(self):
        # short_description has NO player count; about_the_game says "for up to 6 players" / "2-4 friends"
        payload = {
            "550001": {
                "success": True,
                "data": {
                    "steam_appid": 550001,
                    "name": "Deep Parse Coop",
                    "type": "game",
                    "is_free": True,
                    "short_description": "Survive a chaotic haunted kitchen together.",
                    "about_the_game": "<p>Team up with up to 6 friends in frantic online co-op!</p>",
                    "categories": [{"id": 38, "description": "在线合作"}],
                },
            }
        }
        parsed = extractor.parse_appdetails(payload, 550001)
        self.assertEqual(parsed["max_players"], 6)

        # Also test hyphenated "4-player" and "2 to 8 players"
        self.assertEqual(extractor.infer_max_players("A chaotic 4-player co-op heist"), 4)
        self.assertEqual(extractor.infer_max_players("Play with 2 to 8 players online"), 8)

    def test_velocity_delta_followers_boosts_potential_score_and_excludes_old_fullgame_demos(self):
        # 1. Velocity bonus: +400 followers/day boosts potential score significantly
        base = extractor.compute_potential_score(
            current_players=50,
            followers=500,
            age_days=5,
            recent_news_count=1,
            has_schinese=1,
            has_trailer=1,
            delta_followers=0,
            delta_ccu=0,
        )
        surging = extractor.compute_potential_score(
            current_players=50,
            followers=500,
            age_days=5,
            recent_news_count=1,
            has_schinese=1,
            has_trailer=1,
            delta_followers=400,
            delta_ccu=40,
        )
        self.assertGreater(surging, base + 5.0, "Rapid 24h follower/CCU growth must significantly boost potential_score")

        # 2. Old fullgame released >90 days ago with a Demo must NOT enter is_demo_radar
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            db_path = root / "games.sqlite"

            # Seed existing DB with yesterday's metrics & cached short_desc_zh
            conn = db.init_db(db_path)
            db.upsert_game(
                conn,
                {
                    "appid": 5569450,
                    "name": "Fresh Indie Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "online_coop": 1,
                    "current_players": 40,
                    "followers": 200,
                    "short_desc": "Build a cozy campfire with friends.",
                    "short_desc_zh": "和好友一起搭建温馨篝火的联机合作试玩。",
                    "is_demo_radar": 1,
                },
            )
            db.record_demo_metric(conn, 5569450, "2026-11-23", current_players=40, followers=200)
            conn.close()

            # Old fullgame (Factorio-like, released 2020) with a demo -> should stay in games, NOT in demo radar
            (fixture / "427520_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 427520,
                        "name": "Ancient Factory",
                        "type": "game",
                        "is_free": False,
                        "price_overview": {"final": 13000},
                        "release_date": {"coming_soon": False, "date": "2020 年 8 月 14 日"},
                        "demos": [{"appid": 452280}],
                        "categories": [{"id": 38}],
                    }
                ),
                encoding="utf-8",
            )
            # Fresh Demo surging from 200 -> 500 followers and 40 -> 140 CCU
            (fixture / "5569450_appdetails.json").write_text(
                json.dumps(
                    {
                        "appid": 5569450,
                        "name": "Fresh Indie Demo",
                        "type": "demo",
                        "is_free": True,
                        "short_description": "Build a cozy campfire with friends.",
                        "release_date": {"coming_soon": False, "date": "2026 年 11 月 20 日"},
                        "categories": [{"id": 38}],
                    }
                ),
                encoding="utf-8",
            )
            (fixture / "5569450_early.json").write_text(
                json.dumps({"current_players": 140, "followers": 500, "recent_news_count": 2}),
                encoding="utf-8",
            )

            build_snapshot.main(["--limit", "20", "--db", str(db_path), "--offline-fixture", str(fixture)])
            conn = db.init_db(db_path)
            g_old = db.get_game(conn, 427520)
            g_fresh = db.get_game(conn, 5569450)
            conn.close()

            self.assertEqual(g_old["is_demo_radar"], 0, "Fullgame released >90 days ago must NOT pollute new Demo radar")
            self.assertEqual(g_fresh["is_demo_radar"], 1)
            self.assertEqual(g_fresh["delta_followers"], 300)
            self.assertEqual(g_fresh["delta_ccu"], 100)
            # Cached Chinese summary must be preserved when English short_desc hasn't changed!
            self.assertEqual(g_fresh["short_desc_zh"], "和好友一起搭建温馨篝火的联机合作试玩。")

    def test_build_snapshot_carries_forward_followers_on_429_and_translates_english_desc(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "games.sqlite"
            # Pre-seed yesterday's followers=9780
            conn = db.init_db(db_path)
            db.upsert_game(
                conn,
                {
                    "appid": 4895870,
                    "name": "Raccoon Demo",
                    "type": "demo",
                    "is_free": 1,
                    "has_demo": 1,
                    "online_coop": 1,
                    "followers": 9780,
                    "is_demo_radar": 1,
                },
            )
            conn.close()

            fake_search_html = (
                '<a class="search_result_row" data-ds-appid="4895870" data-ds-tagids="[3843]">'
                '<span class="title">Raccoon Demo</span></a>'
            )

            class RateLimitedClient:
                def __init__(self, *args, **kwargs):
                    pass

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
                                "name": "Raccoon Demo",
                                "type": "demo",
                                "is_free": True,
                                "short_description": "Play as mischievous raccoons pulling off chaotic co-op heists.",
                                "release_date": {"coming_soon": False, "date": "2026 年 11 月 20 日"},
                                "categories": [{"id": 38, "description": "在线合作"}],
                            },
                        }
                    }

                def store_page(self, appid):
                    return ""

                def current_players(self, appid):
                    return 320

                def community_followers(self, appid):
                    return None  # Simulated HTTP 429 rate-limit

                def app_news(self, appid, count=5):
                    return []

            with (
                mock.patch.object(build_snapshot, "SteamClient", return_value=RateLimitedClient()),
                mock.patch.object(
                    build_snapshot,
                    "translate_english_descriptions",
                    return_value={4895870: "扮演调皮浣熊组队搞破坏的沙雕联机试玩。"},
                ),
            ):
                build_snapshot.main(["--limit", "10", "--db", str(db_path)])

            conn = db.init_db(db_path)
            g = db.get_game(conn, 4895870)
            conn.close()
            self.assertEqual(g["followers"], 9780, "Rate-limited None followers must carry forward previous count, not clobber to 0")
            self.assertEqual(g["short_desc_zh"], "扮演调皮浣熊组队搞破坏的沙雕联机试玩。")


if __name__ == "__main__":
    unittest.main()
