import json,sqlite3,unittest
from coop_night_picker.constants import REVIEW_TIERS
from coop_night_picker.db import init_db,upsert_game,insert_quote,get_game,recommend_games
from coop_night_picker.extractor import extract_quotes,parse_store_page
class TestData(unittest.TestCase):
 def setUp(self): self.c=init_db(sqlite3.connect(':memory:'))
 def test_roundtrip_and_recommend(self):
  upsert_game(self.c,{'appid':1,'name':'Test','online_coop':1,'categories_json':['在线合作'],'review_score':9,'review_tier_zh':REVIEW_TIERS[9]['zh']})
  self.assertEqual(get_game(self.c,1)['categories_json'],['在线合作']); self.assertEqual(recommend_games(self.c)[0]['appid'],1)
 def test_quote_is_substring(self):
  raw={'review':'和朋友联机很快乐。网络偶尔掉线但还能玩。','votes_up':4}
  for q in extract_quotes(raw): self.assertIn(q['quote_text'],raw['review'])
 def test_store_unescape(self):
  out=parse_store_page('InitAppTagModal(1, [{&quot;tagid&quot;:1667,&quot;name&quot;:&quot;恐怖&quot;}])')
  self.assertEqual(out['tags'][0]['id'],1667)
if __name__=='__main__': unittest.main()
