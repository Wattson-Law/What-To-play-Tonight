from __future__ import annotations
import html,json,re,math
from collections import Counter
from .constants import REVIEW_KEYWORDS
try: from bs4 import BeautifulSoup
except ImportError: BeautifulSoup=None
def parse_search_html(raw):
 if isinstance(raw,dict): raw=raw.get('html') or raw.get('text') or ''
 if BeautifulSoup is None:return []
 s=BeautifulSoup(raw,'html.parser'); out=[]
 for a in s.select('a.search_result_row'):
  try: aid=int(a.get('data-ds-appid','').split(',')[0])
  except: continue
  out.append({'appid':aid,'name':a.select_one('.title').get_text(' ',strip=True) if a.select_one('.title') else '','tag_ids':[int(x) for x in re.findall(r'\d+',a.get('data-ds-tagids',''))]})
 return out
def parse_appdetails(payload,appid=None):
 d=payload.get(str(appid),payload) if isinstance(payload,dict) else {}; d=d.get('data',d) if isinstance(d,dict) else {}; p=d.get('price_overview') or {}; cats=d.get('categories') or []
 return {'appid':appid or d.get('steam_appid'),'name':d.get('name',''),'type':d.get('type','game'),'is_free':int(d.get('is_free',False)),'price_cny':p.get('final',0)/100,'discount_pct':p.get('discount_percent',0),'categories_json':[x.get('description',x) for x in cats],'short_desc':d.get('short_description'),'header_image':d.get('header_image'),'has_demo':int(bool(d.get('demos'))),'demo_appid':(d.get('demos') or [{}])[0].get('appid'),'fullgame_appid':(d.get('fullgame') or {}).get('appid')}
def parse_store_page(raw):
 text=raw if isinstance(raw,str) else raw.get('text',raw.get('__text__',''))
 tags=[]; m=re.search(r'InitAppTagModal\s*\([^,]+,\s*(\[.*?\])\s*\)',text,re.S)
 if m:
  try:
   vals=json.loads(html.unescape(m.group(1))); tags=[{'id':int(x.get('tagid',x.get('id',0))),'name':x.get('name','')} for x in vals if isinstance(x,dict)]
  except: pass
 props=[]
 for node in re.findall(r'<[^>]*data-featuretarget=["\']appreviews["\'][^>]*>',text,re.I|re.S):
  m=re.search(r'data-props=["\'](.*?)["\']',node,re.I|re.S)
  if m:
   try: props.append(json.loads(html.unescape(m.group(1))))
   except: pass
 return {'tags':tags,'review_props':props}
def parse_appreviews(payload):
 if not isinstance(payload,dict): return {'reviews':[],'summary':{}}
 return {'reviews':payload.get('reviews',[]),'summary':payload.get('query_summary',{}),'metadata':{k:v for k,v in payload.items() if k not in {'reviews','query_summary'}}}
def extract_quotes(raw_review,voted_up=True,max_quotes=3,appid=None):
 text=raw_review.get('review','') if isinstance(raw_review,dict) else str(raw_review); votes=int(raw_review.get('votes_up',0)) if isinstance(raw_review,dict) else 0; dims=[]
 scored=[]
 for s in re.split(r'[。！？!？\n]+',text):
  s=s.strip()
  if 6<=len(s)<=80:
   ds=[k for k,p in REVIEW_KEYWORDS.items() if re.search(p,s,re.I)]
   if ds: scored.append((len(ds)*math.log(votes+2),s,ds))
 scored.sort(reverse=True)
 rid=raw_review.get('recommendationid','') if isinstance(raw_review,dict) else ''; author=(raw_review.get('author') or {}).get('steamid','') if isinstance(raw_review,dict) else ''; appid=appid or (raw_review.get('appid','') if isinstance(raw_review,dict) else '')
 url=raw_review.get('review_url') if isinstance(raw_review,dict) else None
 if not url and author and appid: url=f'https://steamcommunity.com/profiles/{author}/recommended/{appid}/'
 out=[]
 for _,s,ds in scored[:max_quotes]:
  assert s in text; out.append({'quote_text':s,'dimensions_json':ds,'voted_up':int(voted_up),'votes_helpful':votes,'recommendation_id':rid,'playtime_hours':round((raw_review.get('author') or {}).get('playtime_at_review',0)/60,2) if isinstance(raw_review,dict) else 0.0,'review_url':url or 'https://steamcommunity.com/'})
 return out
def build_avoid_reasons(reviews):
 c=Counter(d for r in reviews for q in extract_quotes(r,False,99) for d in q['dimensions_json'] if d in {'network','dealbreaker'}); return [f'{k}({v}条提及)' for k,v in c.items()]
def derive_review_summary(props):
    if isinstance(props,list): props=props[0] if props else {}
    if not isinstance(props,dict): return {}
    so=props.get('summary_options',{}) or {}; out={}
    def norm(x):
        if not isinstance(x,dict): return {}
        total=x.get('nReviews',x.get('total_reviews',x.get('cReviews',0))); pos=x.get('nReviewsPositive',x.get('nRecommended',x.get('positive_reviews',x.get('cRecommended',0)))); pct=x.get('fRating',x.get('positive_pct',x.get('percent_positive'))); pct=(100*pos/total if pct is None and total else pct); return {'total':total,'positive':pos,'pct':pct or 0,'score':x.get('eReviewScore',x.get('review_score'))}
    out['global']=norm(so.get('summaryGlobalNoOutliers') or so.get('summaryDisplayed') or props.get('summaryGlobalNoOutliers') or props.get('query_summary',{}))
    out['schinese']=norm(so.get('summaryYourLanguage'))
    for x in (props.get('filter_options',{}) or {}).get('rgLanguageOutliers',[]) or []:
        if str(x.get('strLanguage','')).lower() in {'schinese','chinese'}: out['schinese']=norm(x)
    return out


