from __future__ import annotations
import argparse,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from coop_night_picker.constants import REVIEW_TIERS
REQ={'games':{'appid','name','review_score','review_tier_zh'},'game_quotes':{'appid','quote_text','review_url'}}
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--db',default='data/games.sqlite'); a=p.parse_args(argv); errors=[]
 try: c=sqlite3.connect(a.db); c.row_factory=sqlite3.Row
 except Exception as e: print(f'cannot open db: {e}'); return 1
 for t,cols in REQ.items():
  row=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone()
  if not row: errors.append('missing table '+t); continue
  have={r[1] for r in c.execute(f'PRAGMA table_info({t})')}; errors += [f'missing column {t}.{x}' for x in cols-have]
 idx={r[1] for r in c.execute("PRAGMA index_list('games')")};
 if 'idx_games_filter' not in idx: errors.append('missing idx_games_filter')
 try:
  n=c.execute('SELECT COUNT(*) FROM games').fetchone()[0]; qn=c.execute('SELECT COUNT(*) FROM game_quotes').fetchone()[0]
  for r in c.execute('SELECT review_score,review_tier_zh FROM games'):
   if r['review_score'] not in REVIEW_TIERS or r['review_tier_zh']!=REVIEW_TIERS[r['review_score']]['zh']: errors.append('invalid tier')
  for r in c.execute('SELECT quote_text,review_url FROM game_quotes'):
   if not r['quote_text'] or not r['review_url'].startswith('https://steamcommunity.com/'): errors.append('invalid quote')
  print(f'games={n} quotes={qn}')
 except sqlite3.Error as e: errors.append(str(e))
 if errors: print('\n'.join(errors)); return 1
 return 0
if __name__=='__main__': raise SystemExit(main())
