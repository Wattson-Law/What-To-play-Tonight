import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coop_night_picker import db
from coop_night_picker.constants import REVIEW_TIERS
from coop_night_picker.server import (
    get_game_card,
    log_play,
    recommend_coop_games,
    resolve_game,
)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db_path = root / "games.sqlite"
        self.log_path = root / "user_log.json"
        c = db.init_db(self.db_path)
        db.upsert_game(c, {"appid": 1, "name": "Alpha", "online_coop": 1, "is_free": 1, "max_players": 4, "horror_level": "none", "review_score": 9, "review_tier_zh": REVIEW_TIERS[9]["zh"], "positive_pct": 95, "total_reviews": 100})
        db.upsert_game(c, {"appid": 2, "name": "Scary", "online_coop": 1, "is_free": 1, "max_players": 4, "horror_level": "scary", "review_score": 8, "review_tier_zh": REVIEW_TIERS[8]["zh"]})
        db.upsert_game(c, {"appid": 3, "name": "Avoid", "online_coop": 1, "is_free": 1, "is_avoid": 1, "review_score": 4, "avoid_reasons_json": ["掉线"]})
        db.insert_quote(c, {"appid": 1, "voted_up": 1, "quote_text": "和朋友联机很快乐", "review_url": "https://steamcommunity.com/x"})
        db.insert_quote(c, {"appid": 3, "voted_up": 0, "votes_helpful": 4, "quote_text": "服务器经常掉线", "review_url": "https://steamcommunity.com/avoid"})
        c.close()
        self.patch = patch.multiple("coop_night_picker.server", DB_PATH=self.db_path, USER_LOG_PATH=self.log_path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_need_more_info_and_empty(self):
        self.assertEqual(recommend_coop_games()["status"], "need_more_info")
        self.db_path.unlink()
        self.assertEqual(recommend_coop_games("none", "any", 2, confirmed=True)["status"], "empty_snapshot")

    def test_filter_group_markdown_and_log_exclusion(self):
        out = recommend_coop_games("none", "free_or_demo", 2, confirmed=True)
        self.assertEqual(out["status"], "ok"); self.assertIn("9", out["groups"]); self.assertIn("今晚玩什么", out["markdown_preview"])
        self.assertEqual(log_play(1, "played")["excluded"], True)
        out = recommend_coop_games("none", "free_or_demo", 2, confirmed=True)
        self.assertNotIn(1, [x["appid"] for v in out["groups"].values() for x in v])
        self.assertIn("避雷证据（来自玩家评测）", out["markdown_preview"])

    def test_card_and_resolve_local(self):
        card = get_game_card(1)
        self.assertEqual(card["positive_quotes"][0]["review_url"].startswith("https://"), True)
        self.assertEqual(resolve_game("Alpha", limit=1)["meta"]["source"], "local")


if __name__ == "__main__":
    unittest.main()
