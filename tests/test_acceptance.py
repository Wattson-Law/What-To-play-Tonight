"""Offline acceptance coverage for the complete local MCP surface."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coop_night_picker import db
from coop_night_picker.constants import REVIEW_TIERS
from coop_night_picker import server


class OfflineAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="coop-night-picker-")
        root = Path(self.temp.name)
        self.db_path = root / "games.sqlite"
        self.log_path = root / "user_log.json"
        conn = db.init_db(self.db_path)
        db.upsert_game(conn, {"appid": 10, "name": "Local Hero", "online_coop": 1, "local_coop": 1,
                              "is_free": 1, "max_players": 4, "horror_level": "none", "review_score": 9,
                              "review_tier_zh": REVIEW_TIERS[9]["zh"], "positive_pct": 96, "total_reviews": 120})
        db.upsert_game(conn, {"appid": 11, "name": "Spooky Night", "online_coop": 1,
                              "is_free": 1, "max_players": 4, "horror_level": "scary", "review_score": 8,
                              "review_tier_zh": REVIEW_TIERS[8]["zh"], "positive_pct": 90})
        db.upsert_game(conn, {"appid": 12, "name": "Avoid This", "online_coop": 1, "is_free": 1,
                              "max_players": 4, "horror_level": "none", "review_score": 4,
                              "review_tier_zh": REVIEW_TIERS[4]["zh"], "is_avoid": 1,
                              "avoid_reasons_json": ["掉线"]})
        db.insert_quote(conn, {"appid": 10, "voted_up": 1, "votes_helpful": 5,
                               "quote_text": "和朋友一起玩很开心", "review_url": "https://steamcommunity.com/profiles/10/recommended/10/"})
        db.insert_quote(conn, {"appid": 12, "voted_up": 0, "votes_helpful": 4,
                               "quote_text": "经常掉线", "review_url": "https://steamcommunity.com/profiles/12/recommended/12/"})
        conn.close()
        self.patch = patch.multiple(server, DB_PATH=self.db_path, USER_LOG_PATH=self.log_path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_all_tools_offline(self):
        self.assertEqual(server.recommend_coop_games()["status"], "need_more_info")
        result = server.recommend_coop_games("none", "free_or_demo", 2, confirmed=True)
        self.assertEqual(result["status"], "ok")
        self.assertIn("9", result["groups"])
        self.assertIn("Local Hero", result["markdown_preview"])
        self.assertIn("避雷榜", result["markdown_preview"])
        self.assertEqual(server.get_game_card(10)["status"], "ok")
        self.assertTrue(server.get_game_card(10)["positive_quotes"])
        self.assertEqual(server.resolve_game("Local", limit=1)["meta"]["source"], "local")
        saved = server.log_play(10, "played", "完成一局")
        self.assertTrue(saved["excluded"])
        self.assertEqual(json.loads(self.log_path.read_text(encoding="utf-8"))["records"][0]["appid"], 10)
        after = server.recommend_coop_games("none", "free_or_demo", 2, confirmed=True)
        ids = [g["appid"] for values in after["groups"].values() for g in values]
        self.assertNotIn(10, ids)

    @unittest.skipUnless(server.FastMCP is not None, "optional FastMCP dependency absent")
    def test_fastmcp_registration(self):
        self.assertIsNotNone(server.mcp)


if __name__ == "__main__":
    unittest.main()

