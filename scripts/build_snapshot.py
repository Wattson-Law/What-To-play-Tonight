from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from coop_night_picker.db import init_db,upsert_game,insert_quote
from coop_night_picker.constants import REVIEW_TIERS,HORROR_TAG_IDS
from coop_night_picker.extractor import parse_search_html,parse_appdetails,parse_store_page,parse_appreviews,extract_quotes,build_avoid_reasons
from coop_night_picker.steam_client import SteamClient
def items(x):
 if isinstance(x,dict): return x.get('items') or x.get('results') or parse_search_html(x.get('html') or x.get('text',''))
 return parse_search_html(x)
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=20); p.add_argument('--db',default='data/games.sqlite'); p.add_argument('--with-reviews',action='store_true'); p.add_argument('--offline-fixture'); p.add_argument('--dry-run',action='store_true'); a=p.parse_args(argv); c=init_db(a.db); merged={}; horror=set()
 if a.offline_fixture:
  root=Path(a.offline_fixture)
  for fp in sorted(root.glob('*')):
   raw=fp.read_text(encoding='utf8')
   if fp.suffix.lower()=='.html':
    for it in parse_search_html(raw): merged[it['appid']]=it
   elif fp.suffix.lower()=='.json':
    obj=json.loads(raw)
    if obj.get('appid'): merged[int(obj['appid'])]=obj
    for it in items(obj):
     if it.get('appid'): merged[int(it['appid'])]=it
 else:
  cl=SteamClient()
  for params in ({'category1':998,'category3':38},{'category1':10,'category3':38},{'category1':998,'tags':1667},{'category1':998,'tags':1721},{'category1':998,'tags':3978}):
   for it in items(cl.store_search('',count=a.limit,**params)):
    aid=int(it.get('appid')); merged[aid]=it
    if 'tags' in params: horror.add(aid)
 for aid,seed in list(merged.items())[:a.limit]:
  g=seed.get('game',seed); g.setdefault('appid',aid)
  if not a.offline_fixture:
   d=parse_appdetails(SteamClient().appdetails(aid),aid); g.update(d)
   page=parse_store_page(SteamClient().store_page(aid)); tags=page['tags']; ids={x['id'] for x in tags}; g['tags_json']=[x['name'] for x in tags]; g['horror_level']='scary' if ids & HORROR_TAG_IDS else 'none'; g['online_coop']=int(bool(ids & {1685,3843}) or 38 in [x.get('id') for x in tags]); g['review_score']=g.get('review_score',0)
  g.setdefault('review_tier_zh',REVIEW_TIERS.get(g.get('review_score',0),REVIEW_TIERS[0])['zh'])
  if not a.dry_run: upsert_game(c,g)
  if a.with_reviews and not a.dry_run:
   rv=parse_appreviews(SteamClient().appreviews(aid)); reviews=rv['reviews']; neg=[r for r in reviews if not r.get('voted_up')]; pos=[r for r in reviews if r.get('voted_up')]
   for r in extract_quotes(pos[0],True,3) if pos else []: r.update(appid=aid); insert_quote(c,r)
   for r in extract_quotes(neg[0],False,2) if neg else []: r.update(appid=aid); insert_quote(c,r)
 print(f'processed {min(len(merged),a.limit)} games; db={a.db}; dry_run={a.dry_run}'); return 0
if __name__=='__main__': raise SystemExit(main())
