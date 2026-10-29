from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from coop_night_picker.db import connect,init_db,upsert_game,insert_quote
from coop_night_picker.constants import REVIEW_TIERS
from coop_night_picker.extractor import parse_appdetails,parse_store_page,parse_appreviews,extract_quotes,build_avoid_reasons
from coop_night_picker.steam_client import SteamClient
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=20); p.add_argument('--db',default='data/games.sqlite'); p.add_argument('--with-reviews',action='store_true'); p.add_argument('--offline-fixture'); p.add_argument('--dry-run',action='store_true'); a=p.parse_args(argv)
 c=init_db(a.db); games=[]
 if a.offline_fixture:
  root=Path(a.offline_fixture)
  for fp in sorted(root.glob('*.json'))[:a.limit]:
   obj=json.loads(fp.read_text(encoding='utf8')); appid=int(obj.get('appid') or fp.stem); g=obj.get('game',obj); g.setdefault('appid',appid); games.append(g)
 else:
  client=SteamClient();
  for item in (client.store_search('',category1=998,category3=38,count=a.limit) or {}).get('items',[]):
   appid=int(item.get('appid')); games.append(parse_appdetails(client.appdetails(appid),appid))
 for g in games:
  if not a.dry_run:
   g.setdefault('review_tier_zh',REVIEW_TIERS.get(g.get('review_score',0),REVIEW_TIERS[0])['zh']); upsert_game(c,g)
  if a.with_reviews and not a.dry_run and g.get('reviews'):
   for r in g['reviews']:
    for q in extract_quotes(r,bool(r.get('voted_up')),3):
     q.update(appid=g['appid'],review_url=r.get('review_url','https://steamcommunity.com/')); insert_quote(c,q)
 print(f'processed {len(games)} games; db={a.db}; dry_run={a.dry_run}')
 return 0
if __name__=='__main__': raise SystemExit(main())
