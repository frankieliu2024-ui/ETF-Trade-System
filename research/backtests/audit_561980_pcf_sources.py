#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, re
from pathlib import Path
from urllib.parse import urljoin
import requests

PAGE="https://static.cmfchina.com/web/fundDetail/561980/"
HEAD={"User-Agent":"Mozilla/5.0 ETF-Trade-System research","Accept":"text/html,application/json,text/plain,*/*","Referer":PAGE}
NEEDLES=["申购/赎回清单","申购赎回","pcf","purchase","redeem","composition","成分股","basket","fundCode","fundId","subscribe","redemption","business-service"]
PRIORITY_CHUNKS=["FundDetail.js","FundSubscribeRedemptio.vue.js","FundSubscribeRedemptio.js","FundSubscribeList.vue.js","FundSubscribeList.js","business-service.js","base-service.js","fund.js"]


def req(url,**kwargs):
 r=requests.get(url,headers=HEAD,timeout=25,**kwargs); r.raise_for_status(); return r

def contexts(text,needle,radius=1200):
 out=[]; start=0
 while True:
  p=text.lower().find(needle.lower(),start)
  if p<0 or len(out)>=24: break
  out.append(text[max(0,p-radius):p+radius]); start=p+len(needle)
 return out

def inspect_script(u,a,result):
 try:
  jr=req(u); js=jr.text
  safe=re.sub(r'[^A-Za-z0-9_.-]+','_',u.split('/')[-1]); (a.out/f"script_{safe}").write_text(js,encoding="utf-8")
  hits=[n for n in NEEDLES if n.lower() in js.lower()]
  ctx=[]
  for n in hits: ctx += contexts(js,n)
  urls=sorted(set(re.findall(r'https?://[^"\'\s)]+',js)))[:150]
  paths=sorted(set(re.findall(r'["\']([^"\']*(?:pcf|purchase|redeem|redemption|subscribe|composition|basket|etf|fund)[^"\']*)["\']',js,re.I)))[:600]
  result["scripts"].append({"url":u,"bytes":len(jr.content),"hits":hits,"contexts":ctx[:160],"paths":paths,"urls":urls})
  result["candidate_paths"] += paths; result["candidate_urls"] += urls
  return js
 except Exception as exc:
  result["errors"].append({"stage":"script","url":u,"error":f"{type(exc).__name__}: {exc}"[:300]}); return ""

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
 result={"mode":"RESEARCH_ONLY_561980_PCF_SOURCE_AUDIT","fund_code":"561980","production_context_integration":False,"trade_signal":None,"page":{},"scripts":[],"candidate_paths":[],"candidate_urls":[],"errors":[]}
 try:
  r=req(PAGE); html=r.text; (a.out/"fund_page.html").write_text(html,encoding="utf-8"); result["page"]={"status":r.status_code,"bytes":len(r.content)}
  initial=[urljoin(PAGE,s) for s in re.findall(r'<script[^>]+src=["\']([^"\']+)',html,re.I)]
  seen=set(); discovered=[]
  for u in initial:
   if u in seen: continue
   seen.add(u); js=inspect_script(u,a,result)
   if js:
    for chunk in PRIORITY_CHUNKS:
     if chunk in js: discovered.append(urljoin(u,chunk))
    for rel in re.findall(r'(?:from|import\()["\'](\./[^"\']+\.js)["\']',js):
     if any(k.lower() in rel.lower() for k in ["fundsubscribe","business-service","base-service","fund.js","funddetail"]): discovered.append(urljoin(u,rel))
  roots={u.rsplit('/',1)[0]+'/' for u in initial}
  for root in roots:
   for chunk in PRIORITY_CHUNKS: discovered.append(urljoin(root,chunk))
  # Recursively inspect imports from FundDetail and subscription/redemption chunks.
  queue=sorted(set(discovered))
  while queue:
   u=queue.pop(0)
   if u in seen: continue
   seen.add(u); js=inspect_script(u,a,result)
   if not js: continue
   for rel in re.findall(r'from["\'](\./[^"\']+\.js)["\']',js):
    if any(k.lower() in rel.lower() for k in ["fundsubscribe","business-service","base-service","fund.js"]):
     nu=urljoin(u,rel)
     if nu not in seen: queue.append(nu)
 except Exception as exc:
  result["errors"].append({"stage":"page","url":PAGE,"error":f"{type(exc).__name__}: {exc}"[:300]})
 result["candidate_paths"]=sorted(set(result["candidate_paths"])); result["candidate_urls"]=sorted(set(result["candidate_urls"]))
 (a.out/"pcf_source_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({"script_count":len(result["scripts"]),"candidate_paths":result["candidate_paths"][:100],"candidate_urls":result["candidate_urls"][:50],"errors":result["errors"]},ensure_ascii=False))
 return 0
if __name__=="__main__": raise SystemExit(main())
