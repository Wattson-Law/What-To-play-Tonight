from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from coop_night_picker import constants, db, extractor, kook_bot, server
from coop_night_picker.steam_client import SteamClient


SAMPLE_CURATOR_HTML = """
<div>
  <div role="button" class="recommendation">
    <div>
      <a data-ds-appid="3241660" data-ds-tagids="[1667,3843,1685,492]" class="store_capsule price_inline"
         href="https://store.steampowered.com/app/3241660/REPO/?curator_clanid=41542789">
        <div class="capsule"><img src="https://example.com/repo.jpg" alt="R.E.P.O."></div>
        <div class="discount_block" data-price-final="4200" data-discount="0"></div>
      </a>
    </div>
    <a href="https://store.steampowered.com/app/3241660/REPO/?curator_clanid=41542789" class="recommendation_link">
      <div class="recommendation_midcol">
        <div class="recommendation_stats">
          <div class="recommendation_type_ctn">
            <span class="color_recommended">推荐</span>
            <span class="curator_review_date">10 月 12 日</span>
          </div>
        </div>
        <div class="recommendation_desc">
          物理引擎版致命公司，支持最多 6 人在线合作搬运，队友互坑笑到停不下来，独立恐怖佳作。
        </div>
      </div>
    </a>
  </div>
  <div role="button" class="recommendation">
    <div>
      <a data-ds-appid="999999" data-ds-tagids="[3843,492]" class="store_capsule price_inline"
         href="https://store.steampowered.com/app/999999/BadCoop/?curator_clanid=27093039">
        <div class="capsule"><img src="https://example.com/bad.jpg" alt="BadCoop Demo"></div>
        <div class="discount_block" data-price-final="0" data-discount="0"></div>
      </a>
    </div>
    <a href="https://store.steampowered.com/app/999999/BadCoop/?curator_clanid=27093039" class="recommendation_link">
      <div class="recommendation_midcol">
        <div class="recommendation_stats">
          <div class="recommendation_type_ctn">
            <span class="color_not_recommended">不推荐</span>
            <span class="curator_review_date">10 月 20 日</span>
          </div>
        </div>
        <div class="recommendation_desc">
          联机频繁掉线闪退，试玩版只有半小时半成品内容，快逃别买。
        </div>
      </div>
    </a>
  </div>
</div>
"""


class CuratorPipelineTest(unittest.TestCase):
    def test_curator_sources_and_tag_aliases_defined(self):
        self.assertTrue(hasattr(constants, "CURATOR_SOURCES"))
        clanids = {int(c["clanid"]) for c in constants.CURATOR_SOURCES}
        self.assertIn(6859276, clanids)  # Co-Optimus
        self.assertIn(27093039, clanids)  # 中国鉴赏家同好会
        self.assertIn(41542789, clanids)  # 小黑盒-盒娘后援会
        self.assertIn("indie", constants.TAG_ALIASES)

    def test_steam_client_curator_endpoints(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.path, dict(request.url.params)))
            return httpx.Response(200, json={"success": 1, "total_count": 2, "results_html": SAMPLE_CURATOR_HTML})

        with tempfile.TemporaryDirectory() as tmp:
            client = SteamClient(
                hosts=["https://steamstore-a.akamaihd.net"],
                cache_dir=tmp,
                throttle=0,
                transport=httpx.MockTransport(handler),
            )
            res = client.curator_recommendations(41542789, tagids="3843", start=0, count=20)
            self.assertEqual(res.get("total_count"), 2)
            self.assertEqual(seen[0][0], "/curator/41542789/ajaxgetfilteredrecommendations/")
            self.assertEqual(seen[0][1].get("tagids"), "3843")
            self.assertEqual(seen[0][1].get("l"), "schinese")

    def test_parse_curator_recommendations_and_infer_max_players(self):
        items = extractor.parse_curator_recommendations(
            {"results_html": SAMPLE_CURATOR_HTML},
            curator_name="小黑盒-盒娘后援会",
            clanid=41542789,
        )
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["appid"], 3241660)
        self.assertEqual(first["name"], "R.E.P.O.")
        self.assertEqual(first["attitude"], "recommended")
        self.assertTrue(first["voted_up"])
        self.assertIn("[鉴赏家·小黑盒-盒娘后援会]", first["quote_text"])
        self.assertIn("物理引擎版致命公司", first["quote_text"])
        self.assertIn(492, first["tag_ids"])
        self.assertEqual(first["max_players"], 6)

        second = items[1]
        self.assertEqual(second["appid"], 999999)
        self.assertEqual(second["attitude"], "not_recommended")
        self.assertFalse(second["voted_up"])

        self.assertEqual(extractor.infer_max_players("支持 1-4 人在线联机合作"), 4)
        self.assertEqual(extractor.infer_max_players("双人同屏合作解谜"), 2)
        self.assertEqual(extractor.infer_max_players("Up to 8 players online co-op"), 8)

    def test_db_migration_and_tag_curator_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite"
            raw_conn = sqlite3.connect(db_path)
            # Create old table without curator_count / curators_json
            raw_conn.execute(
                "CREATE TABLE games (appid INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT DEFAULT 'game', "
                "is_free INTEGER DEFAULT 0, has_demo INTEGER DEFAULT 0, demo_appid INTEGER, fullgame_appid INTEGER, "
                "price_cny REAL DEFAULT 0.0, discount_pct INTEGER DEFAULT 0, online_coop INTEGER DEFAULT 0, "
                "local_coop INTEGER DEFAULT 0, remote_play INTEGER DEFAULT 0, max_players INTEGER, "
                "horror_level TEXT DEFAULT 'none', review_score INTEGER DEFAULT 0, review_tier_zh TEXT DEFAULT '评测不足', "
                "total_reviews INTEGER DEFAULT 0, positive_pct REAL DEFAULT 0.0, schinese_score INTEGER, "
                "schinese_pct REAL, schinese_total INTEGER, short_desc TEXT, categories_json TEXT, "
                "tags_json TEXT, avoid_reasons_json TEXT, is_avoid INTEGER DEFAULT 0, header_image TEXT, updated_at TEXT)"
            )
            raw_conn.commit()
            raw_conn.close()

            conn = db.init_db(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
            self.assertIn("curator_count", cols)
            self.assertIn("curators_json", cols)

            db.upsert_game(
                conn,
                {
                    "appid": 1001,
                    "name": "Indie Coop Gem Demo",
                    "online_coop": 1,
                    "has_demo": 1,
                    "is_free": 1,
                    "horror_level": "scary",
                    "max_players": 4,
                    "review_score": 8,
                    "review_tier_zh": "特别好评",
                    "positive_pct": 92.0,
                    "tags_json": ["独立", "在线合作", "恐怖"],
                    "curator_count": 3,
                    "curators_json": ["小黑盒-盒娘后援会", "中国鉴赏家同好会", "Co-Optimus"],
                },
            )
            db.upsert_game(
                conn,
                {
                    "appid": 1002,
                    "name": "AAA Shooter Coop",
                    "online_coop": 1,
                    "has_demo": 0,
                    "is_free": 0,
                    "price_cny": 198.0,
                    "horror_level": "scary",
                    "max_players": 2,
                    "review_score": 8,
                    "review_tier_zh": "特别好评",
                    "positive_pct": 94.0,
                    "tags_json": ["动作", "射击", "在线合作"],
                    "curator_count": 0,
                    "curators_json": [],
                },
            )
            # Filter by indie tag
            indie_rows = db.recommend_games(conn, horror="scary", budget="any", players=4, mode="online", tag="独立")
            self.assertEqual([r["appid"] for r in indie_rows], [1001])

            # Ordering prefers curator_count within tier or overall
            all_rows = db.recommend_games(conn, horror="scary", budget="any", players=2, mode="online")
            self.assertEqual([r["appid"] for r in all_rows], [1001, 1002])
            conn.close()

    def test_server_recommends_with_curator_badges_and_live_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_db = Path(tmp) / "nonexistent.sqlite"
            log_path = Path(tmp) / "user_log.json"

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"success": 1, "total_count": 2, "results_html": SAMPLE_CURATOR_HTML})

            fake_client = SteamClient(
                hosts=["https://steamstore-a.akamaihd.net"],
                cache_dir=str(Path(tmp) / "cache"),
                throttle=0,
                transport=httpx.MockTransport(handler),
            )
            with (
                mock.patch.object(server, "DB_PATH", missing_db),
                mock.patch.object(server, "DEFAULT_DB_PATH", missing_db),
                mock.patch.object(server, "USER_LOG_PATH", log_path),
                mock.patch.object(server, "_download_cloud_snapshot", return_value=False),
                mock.patch.object(server, "SteamClient", return_value=fake_client),
            ):
                res = server.recommend_coop_games(
                    horror="scary",
                    budget="any",
                    players=4,
                    mode="online",
                    tag="独立",
                    confirmed=True,
                )
                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["meta"]["source"], "live_curators")
                self.assertGreaterEqual(res["meta"]["count"], 1)
                self.assertIn("鉴赏家", res["markdown_preview"])
                self.assertIn("R.E.P.O.", res["markdown_preview"])

    def test_kook_bot_session_two_turn_and_direct_recommend(self):
        session = kook_bot.KookBotSession(cooldown_sec=0.0)
        with mock.patch.object(kook_bot, "call_llm_for_params", return_value=None):
            first = session.handle_message("chan-1", "(met)2682988118(met) 今晚玩什么", is_mentioned=True)
            self.assertIn("开黑选游助手", first)
            second = session.handle_message("chan-1", "4人 恐怖 免费 独立", is_mentioned=False)
            self.assertIn("今晚玩什么 · 筛选结果", second)

    def test_kook_bot_handles_role_mention_and_wan_sha_variant(self):
        session = kook_bot.KookBotSession(cooldown_sec=0.0)
        with mock.patch.object(kook_bot, "call_llm_for_params", return_value=None):
            reply = session.handle_message("chan-role", "(rol)77999772(rol) 今晚玩啥", is_mentioned=False)
            self.assertIsNotNone(reply)
            self.assertIn("开黑选游助手", reply)
            direct = session.handle_message("chan-role2", "(rol)77999772(rol) 推荐4人恐怖独立游戏", is_mentioned=False)
            self.assertIsNotNone(direct)
            self.assertIn("今晚玩什么 · 筛选结果", direct)

    def test_friendslop_feeds_negative_blurb_filter_and_kook_formatting(self):
        # 1. FRIENDSLOP_FEEDS must cover popularwishlist, physics+funny, and demos
        self.assertTrue(hasattr(constants, "FRIENDSLOP_FEEDS"))
        feed_filters = [f.get("filter") for f in constants.FRIENDSLOP_FEEDS]
        self.assertIn("popularwishlist", feed_filters)

        # 2. A curator blurb complaining about "阉割" / "赶工作品" / "不耐玩" (like RE3) must be flipped to not_recommended
        re3_html = """
        <div>
          <div role="button" class="recommendation">
            <div>
              <a data-ds-appid="952060" data-ds-tagids="[1667,19]" class="store_capsule price_inline"
                 href="https://store.steampowered.com/app/952060/RE3/?curator_clanid=41542789">
                <div class="capsule"><img src="https://example.com/re3.jpg" alt="Resident Evil 3"></div>
                <div class="discount_block" data-price-final="3960" data-discount="60"></div>
              </a>
            </div>
            <a href="https://store.steampowered.com/app/952060/RE3/?curator_clanid=41542789" class="recommendation_link">
              <div class="recommendation_midcol">
                <div class="recommendation_stats">
                  <div class="recommendation_type_ctn"><span class="color_recommended">推荐</span></div>
                </div>
                <div class="recommendation_desc">
                  在生化2重制版发售一年之后匆匆忙忙推出的赶工作品，场景和剧情大量阉割，过于短促是致命伤，显得非常不耐玩。
                </div>
              </div>
            </a>
          </div>
        </div>
        """
        parsed = extractor.parse_curator_recommendations(re3_html, curator_name="小黑盒-盒娘后援会", clanid=41542789)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["attitude"], "not_recommended")
        self.assertEqual(parsed[0]["voted_up"], 0)

        # 3. Markdown output must NOT contain "####" or >1 decimal float percentages, and must show short_desc gameplay hook
        md = server._markdown(
            {
                "8": [
                    {
                        "appid": 4096830,
                        "name": "S.O.N.A.R. - 潜艇菜鸟团",
                        "is_free": False,
                        "has_demo": True,
                        "price_cny": 29.75,
                        "max_players": 6,
                        "curator_count": 2,
                        "curators": ["Steam物理互坑·欢乐联机热榜", "Steam独立整活·最新发售榜"],
                        "positive_pct": 84.39011138277642,
                        "schinese_pct": 79.26893562181478,
                        "total_reviews": 120,
                        "short_desc": "支持 1–6 人在线合作的混乱潜艇模拟游戏。",
                        "quotes": [],
                    }
                ]
            },
            [],
            4,
            "any",
            "under_50",
        )
        self.assertNotIn("####", md)
        self.assertNotIn("84.390111", md)
        self.assertIn("84.4%", md)
        self.assertIn("79.3%", md)
        self.assertIn("混乱潜艇模拟游戏", md)

        # 4. KOOK intent parser: "我们四个人，玩点恐怖的，50以内的和demo都行" should set budget='under_50' and NOT force tag='demo'
        intent = kook_bot.parse_intent_from_text("@What to play 我们四个人，玩点恐怖的，50以内的和demo都行")
        self.assertEqual(intent["params"].get("budget"), "under_50")
        self.assertIsNone(intent["params"].get("tag"))
