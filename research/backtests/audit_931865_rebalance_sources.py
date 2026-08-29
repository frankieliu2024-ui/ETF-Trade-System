#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, re
from pathlib import Path
import requests

INDEX_CODE = "931865"
HEAD = {
    "User-Agent": "Mozilla/5.0 ETF-Trade-System research",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.csindex.com.cn/",
}

URLS = {
    "basic": f"https://www.csindex.com.cn/csindex-home/indexInfo/index-basic-info/{INDEX_CODE}",
    "details": f"https://www.csindex.com.cn/csindex-home/indexInfo/index-details-data?fileLang=1&indexCode={INDEX_CODE}",
    "top10": f"https://www.csindex.com.cn/csindex-home/index/weight/top10/{INDEX_CODE}",
    "home": "https://www.csindex.com.cn/#/about/news-information",
}

KEYWORDS = ["调样", "调整", "样本", "rebalance", "adjust", "notice", "announcement", "sample"]


def get(url: str):
    r = requests.get(url, headers=HEAD, timeout=25)
    r.raise_for_status()
    return r


def collect_urls(obj):
    out=[]
    if isinstance(obj, dict):
        for v in obj.values(): out.extend(collect_urls(v))
    elif isinstance(obj, list):
        for v in obj: out.extend(collect_urls(v))
    elif isinstance(obj, str) and obj.startswith("http"):
        out.append(obj)
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out", type=Path, required=True); a=ap.parse_args(); a.out.mkdir(parents=True, exist_ok=True)
    result={"mode":"RESEARCH_ONLY_931865_REBALANCE_SOURCE_AUDIT","index_code":INDEX_CODE,"production_context_integration":False,"trade_signal":None,"endpoints":{},"candidate_assets":[],"js_keyword_hits":[],"errors":[]}
    for name,url in URLS.items():
        try:
            r=get(url)
            meta={"status":r.status_code,"content_type":r.headers.get("content-type"),"bytes":len(r.content)}
            if "json" in (r.headers.get("content-type") or ""):
                obj=r.json(); meta["json_top_keys"]=list(obj)[:20] if isinstance(obj,dict) else []
                for u in collect_urls(obj):
                    if INDEX_CODE in u or any(k.lower() in u.lower() for k in ["cons","weight","sample","adjust"]): result["candidate_assets"].append(u)
                (a.out/f"{name}.json").write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
            else:
                text=r.text
                (a.out/f"{name}.html").write_text(text,encoding="utf-8")
                scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)',text,re.I)
                meta["script_count"]=len(scripts)
                for s in scripts:
                    if s.startswith("//"): s="https:"+s
                    elif s.startswith("/"): s="https://www.csindex.com.cn"+s
                    if not s.startswith("http"): continue
                    try:
                        js=get(s).text
                        hits=[]
                        for kw in KEYWORDS:
                            if kw.lower() in js.lower(): hits.append(kw)
                        if hits:
                            paths=sorted(set(re.findall(r'["\']([^"\']*(?:adjust|notice|announcement|sample|rebalance)[^"\']*)["\']',js,re.I)))[:50]
                            result["js_keyword_hits"].append({"script":s,"keywords":hits,"candidate_paths":paths})
                    except Exception as exc:
                        result["errors"].append({"stage":"script","url":s,"error":f"{type(exc).__name__}: {exc}"[:300]})
            result["endpoints"][name]=meta
        except Exception as exc:
            result["errors"].append({"stage":name,"url":url,"error":f"{type(exc).__name__}: {exc}"[:300]})
    result["candidate_assets"]=sorted(set(result["candidate_assets"]))
    (a.out/"rebalance_source_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
