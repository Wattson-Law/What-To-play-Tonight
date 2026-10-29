from __future__ import annotations
import html,json,re,math
from collections import Counter
from .constants import REVIEW_KEYWORDS,TAG_ZH,HORROR_TAG_IDS
try:
 from bs4 import BeautifulSoup
except ImportError: BeautifulSoup=None
def parse_search_html(raw):
 if BeautifulSoup is None:return []
 soup=BeautifulSoup(raw,'html.parser'); out=[]
 for a in soup.select('a.search_result_row'):
  try: appid=int(a.get('data-ds-appid','').split(',')[0])
  except ValueError: continue
  out.append({'appid':appid,'name':a.select_one('.title').get_text(' ',strip=True) if a.select_one('.title') else '', 'tag_ids':[int(x) for x in re.findall(r'\d+',a.get('data-ds-tagids',''))]})
 return out
def parse_appdetails(payload,appid=None):
 data=payload.get(str(appid),payload) if isinstance(payload,dict) else {}; data=data.get('data',data) if isinstance(data,dict) else {}
 p=data.get('price_overview') or {}; cats=data.get('categories') or []
 return {'appid':appid or data.get('steam_appid'),'name':data.get('name',''),'type':data.get('type','game'),'is_free':int(data.get('is_free',False)),'price_cny':p.get('final',0)/100,'discount_pct':p.get('discount_percent',0),'categories_json':[c.get('description',c) for c in cats],'short_desc':data.get('short_description'),'header_image':data.get('header_image')}
def parse_store_page(raw):
 text=raw if isinstance(raw,str) else raw.get('text',''); tags=[]
 m=re.search(r'InitAppTagModal\s*\([^,]+,\s*(\[.*?\])\s*\)',text,re.S)
 if m:
  try:
   vals=json.loads(html.unescape(m.group(1))); tags=[{'id':int(x.get('tagid',x.get('id',0))),'name':x.get('name','')} for x in vals if isinstance(x,dict)]
  except (ValueError,TypeError):pass
 props=[]
 for m in re.finditer(r'data-featuretarget=["\']appreviews["\'][^>]*data-props=["\'](.*?)["\']',text,re.S):
  try: props.append(json.loads(html.unescape(m.group(1))))
  except ValueError: pass
 return {'tags':tags,'review_props':props}
def parse_appreviews(payload):
 return payload.get('reviews',[]) if isinstance(payload,dict) else []
def extract_quotes(raw_review, voted_up=True, max_quotes=3):
 text=raw_review.get('review','') if isinstance(raw_review,dict) else str(raw_review); votes=int(raw_review.get('votes_up',0)) if isinstance(raw_review,dict) else 0
 sentences=[s.strip() for s in re.split(r'[。！？!？\n]+',text) if 6<=len(s.strip())<=80]
 scored=[]
 for s in sentences:
  dims=[k for k,p in REVIEW_KEYWORDS.items() if re.search(p,s,re.I)]
  if dims: scored.append((len(dims)*math.log(votes+2),s,dims))
 scored.sort(reverse=True); return [{'quote_text':s,'dimensions_json':dims,'voted_up':int(voted_up),'votes_helpful':votes} for _,s,dims in scored[:max_quotes] if s in text]
def build_avoid_reasons(reviews):
 c=Counter()
 for r in reviews:
  for q in extract_quotes(r,False,99):
   for d in q['dimensions_json']:
    if d in {'network','dealbreaker'}: c[d]+=1
 return [f'{k}({v}条提及)' for k,v in c.items() if v]
