from __future__ import annotations
import hashlib,json,os,time
from pathlib import Path
try: import httpx
except ImportError: httpx=None
USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'; REDIRECTS={301,302,303,307,308}
class SteamClient:
 def __init__(self,hosts=None,cache_dir='data/http-cache',proxy=None,throttle=1.0,retries=1,transport=None): self.hosts=hosts or ['https://store.steampowered.com','https://steamstore-a.akamaihd.net']; self.cache=Path(cache_dir); self.cache.mkdir(parents=True,exist_ok=True); self.proxy=proxy or os.getenv('STEAM_PROXY'); self.throttle=throttle; self.retries=retries; self.transport=transport; self._last=0
 def _request(self,path,**params):
  key=hashlib.sha256((path+json.dumps(params,sort_keys=True)).encode()).hexdigest(); cp=self.cache/(key+'.json')
  if cp.exists():
   obj=json.loads(cp.read_text(encoding='utf8')); return obj.get('__text__','') if isinstance(obj,dict) and '__text__' in obj else obj
  if httpx is None: return {'ok':False,'unavailable':True,'error':'httpx unavailable'}
  last=None
  for host in self.hosts:
   for attempt in range(self.retries+1):
    time.sleep(max(0,self.throttle-(time.time()-self._last))); self._last=time.time()
    try:
     with httpx.Client(proxy=self.proxy,follow_redirects=False,timeout=20,headers={'User-Agent':USER_AGENT},transport=self.transport) as c: r=c.get(host.rstrip('/')+'/'+path.lstrip('/'),params=params)
     if r.status_code in REDIRECTS: last={'status_code':r.status_code,'location':r.headers.get('location')}; break
     if 200<=r.status_code<300:
      try: data=r.json() if ('json' in r.headers.get('content-type','') or path.startswith(('api/','appreviews/','search/'))) else r.text
      except Exception: data=r.text
      cp.write_text(json.dumps(data,ensure_ascii=False) if not isinstance(data,str) else json.dumps({'__text__':data},ensure_ascii=False),encoding='utf8'); return data
     last={'status_code':r.status_code}
    except Exception as e: last=e
    time.sleep(.1*(2**attempt))
  return {'ok':False,'unavailable':True,**(last if isinstance(last,dict) else {'error':str(last)})}
 def appdetails(self,appid): return self._request('api/appdetails',appids=appid,cc='cn',l='schinese')
 def store_search(self,query,**params):\n  raw=self._request('search/results',term=query,**params)\n  if isinstance(raw,dict) and 'results_html' in raw: return {'items': raw.get('items',[]), 'results_html': raw.get('results_html','')}\n  return raw\n
 search_results=store_search
 def store_page(self,appid): return self._request(f'app/{appid}/',l='schinese',cc='cn')
 def appreviews(self,appid): return self._request(f'appreviews/{appid}',json=1,language='schinese',filter='all',purchase_type='all')
 def tagdata(self,appid=None,lang='schinese'):\n  raw=self._request(f'tagdata/populartags/{lang}')\n  if isinstance(raw,dict): return raw.get('tags',raw.get('data',raw))\n  return raw

