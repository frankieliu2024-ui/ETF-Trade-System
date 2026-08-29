#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, re
from pathlib import Path
from urllib.parse import urljoin
import requests

PAGE="https://static.cmfchina.com/web/fundDetail/561980/"
HEAD={"User-Agent":"Mozilla/5.0 ETF-Trade-System research","Accept":"text/html,application/json,text/plain,*/*","Referer":PAGE}
NEEDLES=["申购/赎回清单","申购赎回","pcf","purchase","redeem","composition","成分股","basket","fundCode","business-service"]
PRIORITY_CHUNKS=["FundDetail.js","FundDetailNalCycleList.js","FundDetailNalCycleList.vue.js","business-service.js","base-service.js","fund.js"]


def req(url,**kwargs):
 r=requests.get(url,headers=HEAD,timeout=25,**kwargs); r.raise_for_status(); return r

def contexts(text,needle,radius=1100):
 out=[]; start=0
 while True:
  p=text.lower().find(needle.lower(),start)
  if p<0 or len(out)>=20: break
  out.append(text[max(0,p-radius):p+radius]); start=p+len(needle)
 return out

def inspect_script(u,a,result):
 try:
  jr=req(u); js=jr.text
  hits=[n for n in NEEDLES if n.lower() in js.lower()]
  if hits:
   ctx=[]
   for n in hits: ctx += contexts(js,n)
   urls=sorted(set(re.findall(r'https?://[^"\'\s)]+',js)))[:100]
   paths=sorted(set(re.findall(r'["\']([^"\']*(?:pcf|purchase|redeem|composition|basket|etf|fund)[^"\']*)["\']',js,re.I)))[:500]
   result["scripts"].append({"url":u,"bytes":len(jr.content),"hits":hits,"contexts":ctx[:120],"paths":paths,"urls":urls})
   result["candidate_paths"] += paths; result["candidate_urls"] += urls
   safe=re.sub(r'[^A-Za-z0-9_.-]+','_',u.split('/')[-1])
   (a.out/f"script_{safe}").write_text(js,encoding="utf-8")
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
    for rel in re.findall(r'import\(["\'](\./[^"\']+\.js)["\']',js):
     if any(k.lower() in rel.lower() for k in ["funddetail","business-service","base-service","fund.js"]): discovered.append(urljoin(u,rel))
  # Entry bundle exposes dynamic imports relative to /ws6/. Probe those exact official chunks too.
  roots=set()
  for u in initial:
   roots.add(u.rsplit('/',1)[0]+'/')
  for root in roots:
   for chunk in PRIORITY_CHUNKS: discovered.append(urljoin(root,chunk))
  for u in sorted(set(discovered)):
   if u in seen: continue
   seen.add(u); inspect_script(u,a,result)
 except Exception as exc:
  result["errors"].append({"stage":"page","url":PAGE,"error":f"{type(exc).__name__}: {exc}"[:300]})
 result["candidate_paths"]=sorted(set(result["candidate_paths"])); result["candidate_urls"]=sorted(set(result["candidate_urls"]))
 (a.out/"pcf_source_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({"script_hits":len(result["scripts"]),"candidate_paths":result["candidate_paths"][:80],"candidate_urls":result["candidate_urls"][:40],"errors":result["errors"]},ensure_ascii=False))
 return 0
if __name__=="__main__": raise SystemExit(main())
