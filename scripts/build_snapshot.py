from __future__ import annotations
import argparse,json,sys,re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from coop_night_picker.db import init_db,upsert_game,insert_quote
from coop_night_picker.constants import REVIEW_TIERS,HORROR_TAG_IDS
from coop_night_picker.extractor import parse_search_html,parse_appdetails,parse_store_page,parse_appreviews,extract_quotes,build_avoid_reasons,derive_review_summary
from coop_night_picker.steam_client import SteamClient
def _items(raw):
 if isinstance(raw,dict): return raw.get('items') or parse_search_html(raw.get('results_html') or raw.get('html') or raw.get('text',''))
 return parse_search_html(raw)
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=20); p.add_argument('--db',default='data/games.sqlite'); p.add_argument('--with-reviews',action='store_true'); p.add_argument('--offline-fixture'); p.add_argument('--dry-run',action='store_true'); a=p.parse_args(argv); c=init_db(a.db); merged={}; root=Path(a.offline_fixture) if a.offline_fixture else None; client=SteamClient();
 if root:
  for fp in sorted(root.glob('*')):
   if fp.suffix.lower()=='.html':
    for x in parse_search_html(fp.read_text(encoding='utf8')): merged[x['appid']]=x
   elif fp.suffix.lower()=='.json':
    o=json.loads(fp.read_text(encoding='utf8')); aid=o.get('appid') or (int(fp.stem) if fp.stem.isdigit() else None)
    if aid: merged[int(aid)]=o
 else:
  for q in ({'category1':998,'category3':38},{'category1':10,'category3':38},{'category1':998,'tags':3843},{'category1':998,'tags':1685},{'category1':998,'tags':1667},{'category1':998,'tags':1721},{'category1':998,'tags':3978}):
   for x in _items(client.store_search('',count=a.limit,**q)): merged[int(x['appid'])]=x
 for aid,seed in list(merged.items())[:a.limit]:
  g=dict(seed.get('game',seed)); g['appid']=aid
  def fixture(kind):
   if not root:return None
   for name in (f'{aid}_{kind}.json',f'{aid}_{kind}.html',f'{aid}.{kind}.json',f'{aid}.{kind}.html'):
    f=root/name
    if f.exists(): return f.read_text(encoding='utf8')
   return None
  d=parse_appdetails(json.loads(fixture('appdetails')),aid) if fixture('appdetails') else (parse_appdetails(client.appdetails(aid),aid) if not root else {}) ; g.update(d)
  page=parse_store_page(fixture('store') or fixture('store_page') or (client.store_page(aid) if not root else '')); tags=page['tags']; ids={x['id'] for x in tags}; g['tags_json']=[x['name'] for x in tags]; g['horror_level']='scary' if ({1667,3978} & ids) else ('mild' if 1721 in ids else 'none'); cats=set(g.get('categories_json') or []); g['online_coop']=int(38 in ids or 3843 in ids or '在线合作' in cats); g['local_coop']=int(39 in ids or 3841 in ids or '同屏/分屏合作' in cats); g['remote_play']=int(44 in ids or '远程同乐' in cats)
  sm=derive_review_summary(page['review_props']); glob=sm.get('global',{}); sch=sm.get('schinese',{}); g['total_reviews']=glob.get('total',g.get('total_reviews',0)); g['positive_pct']=glob.get('pct',g.get('positive_pct',0)); g['schinese_total']=sch.get('total',sch.get('total_reviews')); g['schinese_pct']=sch.get('pct',sch.get('positive_pct')); g['review_score']=glob.get('score') or g.get('review_score',0); g['schinese_score']=sch.get('score'); g['review_tier_zh']=REVIEW_TIERS.get(g['review_score'],REVIEW_TIERS[0])['zh']
  reviews=[]
  if a.with_reviews:
   raw=fixture('reviews'); rv=parse_appreviews(json.loads(raw)) if raw else parse_appreviews(client.appreviews(aid)); reviews=rv['reviews']
  pos=[r for r in reviews if r.get('voted_up')]; neg=[r for r in reviews if not r.get('voted_up')]; reasons=build_avoid_reasons(neg); g['avoid_reasons_json']=reasons; g['is_avoid']=int(g['review_score']<=5 or (g.get('schinese_pct') is not None and g.get('positive_pct') is not None and g['positive_pct']-g['schinese_pct']>=15) or len(reasons)>=3)
  if not a.dry_run:
   upsert_game(c,g)
   for group,cap in ((pos,3),(neg,2)):
    allq=[]
    for rr in group:
     allq.extend(extract_quotes(rr,bool(rr.get('voted_up')),99,appid=aid))
    for q in allq[:cap]:
     if q.get('review_url','').startswith('https://steamcommunity.com/profiles/'):
      q['appid']=aid; insert_quote(c,q)
 print(f'processed {min(a.limit,len(merged))} games; db={a.db}; dry_run={a.dry_run}'); return 0
if __name__=='__main__': raise SystemExit(main())








