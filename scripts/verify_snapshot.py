from __future__ import annotations
import argparse,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from coop_night_picker.constants import REVIEW_TIERS
REQ={'games':{'appid','name','review_score','review_tier_zh'},'game_quotes':{'appid','quote_text','review_url'}}
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--db',default='data/games.sqlite'); a=p.parse_args(argv); e=[]
 try:c=sqlite3.connect(a.db); c.row_factory=sqlite3.Row
 except Exception as x: print(x); return 1
 for t,cols in REQ.items():
  if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone(): e.append('missing table '+t); continue
  have={r[1] for r in c.execute(f'PRAGMA table_info({t})')}; e += [f'missing column {t}.{x}' for x in cols-have]
 if 'idx_games_filter' not in {r[1] for r in c.execute("PRAGMA index_list('games')")}: e.append('missing idx_games_filter')
 if 'idx_quotes_appid' not in {r[1] for r in c.execute("PRAGMA index_list('game_quotes')")}: e.append('missing idx_quotes_appid')
 try:
  tiers={}
  for r in c.execute('SELECT review_score,review_tier_zh FROM games'):
   tiers[r[0]]=tiers.get(r[0],0)+1
   if r[0] not in REVIEW_TIERS or r[1]!=REVIEW_TIERS[r[0]]['zh']: e.append('invalid tier')
  for r in c.execute('SELECT quote_text,review_url FROM game_quotes'):
   if not r[0] or not r[1].startswith('https://steamcommunity.com/'): e.append('invalid quote')
  print(f"games={c.execute('SELECT COUNT(*) FROM games').fetchone()[0]} quotes={c.execute('SELECT COUNT(*) FROM game_quotes').fetchone()[0]} tiers={tiers}")
 except Exception as x:e.append(str(x))
 if e: print('\n'.join(e)); return 1
 return 0
if __name__=='__main__': raise SystemExit(main())
