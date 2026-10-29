from __future__ import annotations
import hashlib,json,os,time
from pathlib import Path
from typing import Any
try:
 import httpx
except ImportError: httpx=None
from .probe import USER_AGENT
REDIRECTS={301,302,303,307,308}
class SteamClient:
 def __init__(self,hosts=None,cache_dir='data/http-cache',proxy=None,throttle=1.0,retries=2,transport=None):
  self.hosts=hosts or ['https://store.steampowered.com','https://steamstore-a.akamaihd.net']; self.cache=Path(cache_dir); self.cache.mkdir(parents=True,exist_ok=True); self.proxy=proxy or os.getenv('STEAM_PROXY'); self.throttle=throttle; self.retries=retries; self._last=0; self.transport=transport
 def _request(self,path,**params):
  key=hashlib.sha256((path+json.dumps(params,sort_keys=True)).encode()).hexdigest(); cp=self.cache/(key+'.json')
  if cp.exists(): return json.loads(cp.read_text(encoding='utf8'))
  if httpx is None: raise RuntimeError('httpx is required for live requests')
  for host in self.hosts:
   url=host.rstrip('/')+'/'+path.lstrip('/'); last=None
   for attempt in range(self.retries+1):
    time.sleep(max(0,self.throttle-(time.time()-self._last))); self._last=time.time()
    try:
     with httpx.Client(proxy=self.proxy,follow_redirects=False,timeout=20,headers={'User-Agent':USER_AGENT},transport=self.transport) as c: r=c.get(url,params=params)
     if r.status_code in REDIRECTS: return {'ok':False,'unavailable':True,'status_code':r.status_code,'location':r.headers.get('location'),'url':str(r.url)}
     if 200<=r.status_code<300:
      ct=r.headers.get('content-type',''); data=r.json() if 'json' in ct or path.startswith(('api/','appreviews/')) else r.text
      cp.write_text(json.dumps(data,ensure_ascii=False) if not isinstance(data,str) else json.dumps({'text':data},ensure_ascii=False),encoding='utf8'); return data
     last=r
    except Exception as e: last=e
    time.sleep(0.25*(2**attempt))
  return {'ok':False,'unavailable':True,'error':str(last)}
 def appdetails(self,appid): return self._request('api/appdetails',appids=appid,cc='cn',l='schinese')
 def store_search(self,query,**params): return self._request('search/results',term=query,**params)
 search_results=store_search
 def store_page(self,appid): return self._request(f'app/{appid}/',l='schinese',cc='cn')
 def appreviews(self,appid): return self._request(f'appreviews/{appid}',json=1,language='schinese',filter='all',purchase_type='all')
 def tagdata(self,appid): return self.store_page(appid)
