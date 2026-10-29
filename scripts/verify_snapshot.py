from __future__ import annotations
import argparse,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from coop_night_picker.constants import REVIEW_TIERS
from coop_night_picker.db import connect,init_db
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--db',default='data/games.sqlite'); a=p.parse_args(argv); c=init_db(a.db); errors=[]
 for key in ('games','game_quotes'):
  if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(key,)).fetchone(): errors.append('missing '+key)
 for r in c.execute('SELECT review_score,review_tier_zh FROM games'):
  if r[1] != REVIEW_TIERS.get(r[0],REVIEW_TIERS[0])['zh']: errors.append('tier mismatch')
 for q in c.execute('SELECT quote_text,review_url FROM game_quotes'):
  if not q[0] or not q[1].startswith('https://steamcommunity.com/'): errors.append('bad quote')
 print(f'games={c.execute("SELECT COUNT(*) FROM games").fetchone()[0]} quotes={c.execute("SELECT COUNT(*) FROM game_quotes").fetchone()[0]}')
 if errors:
  print('\n'.join(errors)); return 1
 return 0
if __name__=='__main__': raise SystemExit(main())
