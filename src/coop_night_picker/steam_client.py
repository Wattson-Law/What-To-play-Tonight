from __future__ import annotations
import os,json,time,hashlib
from pathlib import Path
try: import httpx
except ImportError: httpx=None
class SteamClient:
 def __init__(self,hosts=None,cache_dir='data/http-cache',proxy=None,throttle=1.0,retries=1,transport=None):
  self.hosts=hosts or ([os.getenv('STEAM_HOST')] if os.getenv('STEAM_HOST') else ['https://store.steampowered.com','https://steamstore-a.akamaihd.net']); self.cache=Path(os.getenv('STEAM_CACHE_DIR',cache_dir)); self.cache.mkdir(parents=True,exist_ok=True); self.proxy=proxy or os.getenv('STEAM_PROXY'); self.throttle=throttle; self.retries=retries; self.transport=transport; self._last=0
 def _request(self,path,**params):
  cp=self.cache/(hashlib.sha256((path+json.dumps(params,sort_keys=True)).encode()).hexdigest()+'.json')
  if cp.exists():
   x=json.loads(cp.read_text(encoding='utf8')); return x.get('__text__','') if isinstance(x,dict) and '__text__' in x else x
  if httpx is None:return {'ok':False,'unavailable':True}
  for host in self.hosts:
   try:
    time.sleep(max(0,self.throttle-(time.time()-self._last)))
    self._last=time.time()
    with httpx.Client(proxy=self.proxy,follow_redirects=False,timeout=20,headers={"User-Agent":"Mozilla/5.0"},transport=self.transport) as c: r=c.get(host.rstrip("/")+"/"+path.lstrip("/"),params=params)
    if r.status_code in {301,302,303,307,308}: continue
    if 200<=r.status_code<300:
     try:d=r.json()
     except Exception:d=r.text
     cp.write_text(json.dumps(d,ensure_ascii=False) if not isinstance(d,str) else json.dumps({'__text__':d},ensure_ascii=False),encoding='utf8'); return d
   except Exception: pass
  return {'ok':False,'unavailable':True}
 def appdetails(self,appid):return self._request('api/appdetails',appids=appid,cc='cn',l='schinese')
 def store_search(self,query,**params):return self._request('search/results',term=query,**params)
 search_results=store_search
 def store_page(self,appid):return self._request(f'app/{appid}/',l='schinese',cc='cn')
 def appreviews(self,appid,review_type=None,playtime_filter_min=1):
  p={'json':1,'language':'schinese','filter':'all','purchase_type':'all','num_per_page':50,'playtime_filter_min':playtime_filter_min}
  if review_type:p['review_type']=review_type
  return self._request(f'appreviews/{appid}',**p)
 def tagdata(self,appid=None,lang='schinese'):return self._request(f'tagdata/populartags/{lang}')

