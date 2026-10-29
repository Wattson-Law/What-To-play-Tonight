from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from coop_night_picker import db
from coop_night_picker.extractor import parse_appdetails
from coop_night_picker.steam_client import SteamClient
from scripts import build_snapshot


class SteamClientRegressionTest(unittest.TestCase):
    def test_retries_failed_request_and_keeps_review_parameters(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(200, json={"success": 1}, request=request)

        client = SteamClient(
            hosts=["https://example.test"],
            cache_dir=tempfile.mkdtemp(),
            throttle=0,
            retries=1,
            transport=httpx.MockTransport(handler),
        )
        with patch("coop_night_picker.steam_client.time.sleep"):
            self.assertEqual(client.appreviews(42, review_type="negative"), {"success": 1})

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].url.path, "/appreviews/42")
        self.assertEqual(calls[0].url.params["review_type"], "negative")
        self.assertEqual(calls[0].url.params["language"], "schinese")
        self.assertEqual(calls[0].headers["user-agent"], "Mozilla/5.0")


class SnapshotRegressionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="coop-night-picker-regression-")
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        self.db_path = self.root / "games.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def _write_game(self, appid: int, *, game_type: str = "game"):
        (self.fixture / f"{appid}_appdetails.json").write_text(
            json.dumps(
                {
                    "appid": appid,
                    "name": f"Game {appid}",
                    "type": game_type,
                    "is_free": True,
                    "categories": [{"id": 38, "description": "Co-op"}],
                    "demos": [],
                    "fullgame": {"appid": 1} if game_type == "demo" else {},
                }
            ),
            encoding="utf-8",
        )

    def test_build_separates_reviews_cleans_stale_rows_and_uses_category_ids(self):
        self._write_game(123)
        (self.fixture / "123_reviews_positive.json").write_text(
            json.dumps(
                {
                    "reviews": [
                        {
                            "review": "和朋友联机很快乐，配合特别欢乐。",
                            "voted_up": True,
                            "votes_up": 8,
                            "recommendationid": "positive-1",
                            "author": {"steamid": "100"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.fixture / "123_reviews_negative.json").write_text(
            json.dumps(
                {
                    "reviews": [
                        {
                            "review": "经常掉线，服务器连不上，快逃。",
                            "votes_up": 7,
                            "recommendationid": "negative-1",
                            "author": {"steamid": "101"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        conn = db.init_db(self.db_path)
        db.upsert_game(conn, {"appid": 999, "name": "Stale"})
        db.insert_quote(
            conn,
            {
                "appid": 999,
                "voted_up": 0,
                "quote_text": "旧数据",
                "review_url": "https://steamcommunity.com/profiles/999/recommended/999/",
            },
        )
        conn.close()

        build_snapshot.main(
            [
                "--limit",
                "20",
                "--db",
                str(self.db_path),
                "--with-reviews",
                "--offline-fixture",
                str(self.fixture),
            ]
        )

        conn = db.init_db(self.db_path)
        game = db.get_game(conn, 123)
        quotes = conn.execute("SELECT voted_up FROM game_quotes WHERE appid=123 ORDER BY id").fetchall()
        stale_game = db.get_game(conn, 999)
        stale_quotes = conn.execute("SELECT COUNT(*) FROM game_quotes WHERE appid=999").fetchone()[0]
        conn.close()
        self.assertEqual(game["online_coop"], 1)
        self.assertIsNone(stale_game)
        self.assertEqual({row[0] for row in quotes}, {0, 1})
        self.assertEqual(stale_quotes, 0)

    def test_demo_type_is_also_marked_as_demo(self):
        self._write_game(124, game_type="demo")
        build_snapshot.main(
            [
                "--limit",
                "20",
                "--db",
                str(self.db_path),
                "--offline-fixture",
                str(self.fixture),
            ]
        )
        conn = db.init_db(self.db_path)
        game = db.get_game(conn, 124)
        conn.close()
        self.assertEqual(game["has_demo"], 1)

    def test_unknown_review_score_does_not_create_avoid_flag(self):
        self._write_game(125)
        build_snapshot.main(
            [
                "--limit",
                "20",
                "--db",
                str(self.db_path),
                "--offline-fixture",
                str(self.fixture),
            ]
        )
        conn = db.init_db(self.db_path)
        game = db.get_game(conn, 125)
        conn.close()
        self.assertEqual(game["review_score"], 0)
        self.assertEqual(game["is_avoid"], 0)


class ExtractorRegressionTest(unittest.TestCase):
    def test_category_ids_are_preserved_for_fallback_classification(self):
        parsed = parse_appdetails({"name": "Co-op", "categories": [{"id": 38, "description": "Co-op"}]}, 1)
        self.assertEqual(parsed["category_ids"], [38])


if __name__ == "__main__":
    unittest.main()
