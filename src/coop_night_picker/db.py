from __future__ import annotations
import json, sqlite3
from pathlib import Path
from typing import Any
SCHEMA='''CREATE TABLE IF NOT EXISTS games (appid INTEGER PRIMARY KEY,name TEXT NOT NULL,type TEXT DEFAULT 'game',is_free INTEGER DEFAULT 0,has_demo INTEGER DEFAULT 0,demo_appid INTEGER,fullgame_appid INTEGER,price_cny REAL DEFAULT 0.0,discount_pct INTEGER DEFAULT 0,online_coop INTEGER DEFAULT 0,local_coop INTEGER DEFAULT 0,remote_play INTEGER DEFAULT 0,max_players INTEGER,horror_level TEXT DEFAULT 'none',review_score INTEGER DEFAULT 0,review_tier_zh TEXT DEFAULT '评测不足',total_reviews INTEGER DEFAULT 0,positive_pct REAL DEFAULT 0.0,schinese_score INTEGER,schinese_pct REAL,schinese_total INTEGER,short_desc TEXT,categories_json TEXT,tags_json TEXT,avoid_reasons_json TEXT,is_avoid INTEGER DEFAULT 0,header_image TEXT,updated_at TEXT);
CREATE TABLE IF NOT EXISTS game_quotes (id INTEGER PRIMARY KEY AUTOINCREMENT,appid INTEGER NOT NULL,recommendation_id TEXT,voted_up INTEGER NOT NULL,votes_helpful INTEGER DEFAULT 0,playtime_hours REAL DEFAULT 0.0,quote_text TEXT NOT NULL,dimensions_json TEXT,review_url TEXT NOT NULL,FOREIGN KEY (appid) REFERENCES games(appid));
CREATE INDEX IF NOT EXISTS idx_games_filter ON games(online_coop,horror_level,is_free,has_demo,review_score DESC);
CREATE INDEX IF NOT EXISTS idx_quotes_appid ON game_quotes(appid,voted_up);'''
JSON_FIELDS={'categories_json','tags_json','avoid_reasons_json','dimensions_json'}
def connect(path: str|Path='data/games.sqlite')->sqlite3.Connection:
 p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(p); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def init_db(conn_or_path):
 c=connect(conn_or_path) if isinstance(conn_or_path,(str,Path)) else conn_or_path; c.row_factory=sqlite3.Row; c.executescript(SCHEMA); c.commit(); return c
def _encode(d):
 return {k:(json.dumps(v,ensure_ascii=False) if k in JSON_FIELDS and not isinstance(v,str) else v) for k,v in d.items()}
def _decode(row):
 if row is None:return None
 d=dict(row)
 for k in JSON_FIELDS:
  if k in d and d[k]:
   try:d[k]=json.loads(d[k])
   except (TypeError,json.JSONDecodeError):pass
 return d
def upsert_game(conn, game):
 fields=[r[1] for r in conn.execute('PRAGMA table_info(games)')]; d={k:v for k,v in _encode(dict(game)).items() if k in fields}; d.setdefault('name',str(d.get('appid',''))); cols=list(d); conn.execute(f"INSERT INTO games ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) ON CONFLICT(appid) DO UPDATE SET "+','.join(f'{k}=excluded.{k}' for k in cols if k!='appid'),[d[k] for k in cols]); conn.commit()
def insert_quote(conn, quote):
 d=_encode(dict(quote)); cols=[k for k in d if k in {'appid','recommendation_id','voted_up','votes_helpful','playtime_hours','quote_text','dimensions_json','review_url'}]; conn.execute(f"INSERT INTO game_quotes ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",[d[k] for k in cols]); conn.commit()
def get_game(conn, appid): return _decode(conn.execute('SELECT * FROM games WHERE appid=?',(appid,)).fetchone())
def search_games(conn, query, limit=20): return [_decode(r) for r in conn.execute('SELECT * FROM games WHERE name LIKE ? ORDER BY review_score DESC LIMIT ?',('%'+query+'%',limit))]
def recommend_games(conn, *, horror='any', budget='any', players=None, mode='online', limit=50):
 sql='SELECT * FROM games WHERE is_avoid=0'; args=[]
 if horror in {'none','mild','scary'}: sql+=' AND horror_level=?'; args.append(horror)
 if budget=='free_or_demo': sql+=' AND (is_free=1 OR has_demo=1)'
 elif budget=='under_50': sql+=' AND price_cny<50'
 if players: sql+=' AND (max_players IS NULL OR max_players>=?)'; args.append(players)
 if mode=='online': sql+=' AND online_coop=1'
 elif mode=='local': sql+=' AND local_coop=1'
 sql+=' ORDER BY review_score DESC, positive_pct DESC LIMIT ?'; args.append(limit)
 return [_decode(r) for r in conn.execute(sql,args)]
def get_avoid_list(conn, limit=20): return [_decode(r) for r in conn.execute('SELECT * FROM games WHERE is_avoid=1 ORDER BY review_score ASC LIMIT ?',(limit,))]

