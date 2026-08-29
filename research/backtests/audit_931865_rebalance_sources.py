#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, re
from pathlib import Path
import requests

INDEX_CODE = "931865"
INDEX_NAME = "中证半导体产业指数"
HEAD = {"User-Agent":"Mozilla/5.0 ETF-Trade-System research","Accept":"application/json,text/plain,*/*","Referer":"https://www.csindex.com.cn/"}
URLS={
 "basic":f"https://www.csindex.com.cn/csindex-home/indexInfo/index-basic-info/{INDEX_CODE}",
 "details":f"https://www.csindex.com.cn/csindex-home/indexInfo/index-details-data?fileLang=1&indexCode={INDEX_CODE}",
 "top10":f"https://www.csindex.com.cn/csindex-home/index/weight/top10/{INDEX_CODE}",
 "home":"https://www.csindex.com.cn/#/about/news-information",
}
ANN="https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementByVo"
KEYWORDS=["调样","调整","样本","rebalance","adjust","notice","announcement","sample"]


def get(url:str,**kwargs):
 r=requests.get(url,headers=HEAD,timeout=25,**kwargs); r.raise_for_status(); return r

def collect_urls(obj):
 out=[]
 if isinstance(obj,dict):
  for v in obj.values(): out.extend(collect_urls(v))
 elif isinstance(obj,list):
  for v in obj: out.extend(collect_urls(v))
 elif isinstance(obj,str) and obj.startswith("http"): out.append(obj)
 return out

def response_meta(r):
 m={"status":r.status_code,"content_type":r.headers.get("content-type"),"bytes":len(r.content),"body_prefix":r.text[:300]}
 try:
  o=r.json(); m["json_top_keys"]=list(o)[:20] if isinstance(o,dict) else []; m["json"]=o
 except Exception: pass
 return m

def probe_announcements():
 probes=[]
 candidates=[{"pageNum":1,"pageSize":100,"title":INDEX_CODE},{"page":1,"rows":100,"title":INDEX_CODE},{"pageNum":1,"pageSize":100,"keyWord":INDEX_CODE}]
 for payload in candidates:
  for method in ["GET","POST_JSON","POST_FORM"]:
   try:
    if method=="GET": r=requests.get(ANN,params=payload,headers=HEAD,timeout=12)
    elif method=="POST_JSON": r=requests.post(ANN,json=payload,headers=HEAD,timeout=12)
    else: r=requests.post(ANN,data=payload,headers=HEAD,timeout=12)
    m=response_meta(r); m.update({"method":method,"payload":payload}); probes.append(m)
   except Exception as exc: probes.append({"method":method,"payload":payload,"error":f"{type(exc).__name__}: {exc}"[:300]})
 return probes

def contexts(js:str,needles:list[str],radius:int=1400):
 out=[]
 for needle in needles:
  start=0
  while True:
   pos=js.find(needle,start)
   if pos<0: break
   out.append({"needle":needle,"position":pos,"context":js[max(0,pos-radius):pos+radius]})
   start=pos+len(needle)
   if len([x for x in out if x["needle"]==needle])>=12: break
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
 result={"mode":"RESEARCH_ONLY_931865_REBALANCE_SOURCE_AUDIT","index_code":INDEX_CODE,"production_context_integration":False,"trade_signal":None,"endpoints":{},"candidate_assets":[],"js_keyword_hits":[],"announcement_probes":[],"errors":[]}
 for name,url in URLS.items():
  try:
   r=get(url); meta={"status":r.status_code,"content_type":r.headers.get("content-type"),"bytes":len(r.content)}
   if "json" in (r.headers.get("content-type") or ""):
    obj=r.json(); meta["json_top_keys"]=list(obj)[:20] if isinstance(obj,dict) else []
    for u in collect_urls(obj):
     if INDEX_CODE in u or any(k in u.lower() for k in ["cons","weight","sample","adjust"]): result["candidate_assets"].append(u)
    (a.out/f"{name}.json").write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
   else:
    text=r.text; (a.out/f"{name}.html").write_text(text,encoding="utf-8")
    scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)',text,re.I); meta["script_count"]=len(scripts)
    for idx,s in enumerate(scripts):
     if s.startswith("//"): s="https:"+s
     elif s.startswith("/"): s="https://www.csindex.com.cn"+s
     if not s.startswith("http"): continue
     try:
      js=get(s).text
      if "app." in s: (a.out/"csindex_app.js").write_text(js,encoding="utf-8")
      hits=[kw for kw in KEYWORDS if kw.lower() in js.lower()]
      if hits:
       paths=sorted(set(re.findall(r'["\']([^"\']*(?:adjust|notice|announcement|sample|rebalance)[^"\']*)["\']',js,re.I)))[:120]
       ctx=contexts(js,["queryAnnouncementByVo","queryAnnouncementByVonew","currentPage","pageSize","announcementType","sampleSearch","list.of.application.for.sample.adjustment"])
       result["js_keyword_hits"].append({"script":s,"keywords":hits,"candidate_paths":paths,"contexts":ctx})
     except Exception as exc: result["errors"].append({"stage":"script","url":s,"error":f"{type(exc).__name__}: {exc}"[:300]})
   result["endpoints"][name]=meta
  except Exception as exc: result["errors"].append({"stage":name,"url":url,"error":f"{type(exc).__name__}: {exc}"[:300]})
 result["announcement_probes"]=probe_announcements(); result["candidate_assets"]=sorted(set(result["candidate_assets"]))
 (a.out/"rebalance_source_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({"index_code":INDEX_CODE,"candidate_assets":result["candidate_assets"],"announcement_probe_count":len(result["announcement_probes"]),"errors":result["errors"]},ensure_ascii=False))
 return 0
if __name__=="__main__": raise SystemExit(main())
