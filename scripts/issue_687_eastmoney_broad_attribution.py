#!/usr/bin/env python3
"""Read-only Issue #687 Eastmoney broad attribution probe.

Tests whether SSE-official identities omitted by the production Eastmoney ETF_FS
can be enumerated by a bounded set of broader, documented-in-response Eastmoney
market selectors. Writes artifact only; never mutates canonical state.
"""
from __future__ import annotations
import argparse, json, urllib.parse, urllib.request
from pathlib import Path

URL="https://push2.eastmoney.com/api/qt/clist/get"
CURRENT_FS="b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827"
# Bounded exchange-wide selectors, not guessed MK bucket IDs. They answer whether
# the identities exist in Eastmoney's broad security enumeration at all.
SELECTORS={"SH_ALL":"m:1+t:2,m:1+t:23","SZ_ALL":"m:0+t:6,m:0+t:80"}
FIELDS="f12,f13,f14"

def fetch(fs: str):
    rows=[]; pn=1
    while pn<=80:
        params={"pn":pn,"pz":500,"po":1,"np":1,"fltt":2,"invt":2,"fid":"f12","fs":fs,"fields":FIELDS}
        req=urllib.request.Request(URL+"?"+urllib.parse.urlencode(params),headers={"User-Agent":"Mozilla/5.0","Referer":"https://quote.eastmoney.com/"})
        with urllib.request.urlopen(req,timeout=20) as r:
            payload=json.loads(r.read().decode("utf-8"))
        diff=((payload.get("data") or {}).get("diff") or [])
        if not diff: break
        rows.extend(diff)
        total=int((payload.get("data") or {}).get("total") or 0)
        if len(rows)>=total: break
        pn+=1
    return rows

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--output",required=True); a=ap.parse_args()
    q=json.loads(Path("data/state/query_context.json").read_text(encoding="utf-8"))
    rec=((q.get("formal_etf_discovery") or {}).get("broad_source_reconciliation") or {})
    targets=rec.get("official_only_identities") or []
    target_codes={str(x.get("code") or "") for x in targets if x.get("code")}
    current={str(x.get("f12") or "") for x in fetch(CURRENT_FS)}
    probes={}
    union=set()
    errors={}
    for name,fs in SELECTORS.items():
        try:
            codes={str(x.get("f12") or "") for x in fetch(fs)}
            probes[name]={"row_count":len(codes),"target_hits":sorted(target_codes & codes)}
            union |= codes
        except Exception as e:
            errors[name]=repr(e)
    found=sorted(target_codes & union)
    out={
      "schema_version":"1.0","issue":687,"read_only":True,
      "purpose":"attribute SSE-official-only identities against bounded Eastmoney broad enumeration",
      "production_current_fs":CURRENT_FS,
      "target_count":len(target_codes),
      "target_codes":sorted(target_codes),
      "current_fs_target_hits":sorted(target_codes & current),
      "selectors":probes,"selector_errors":errors,
      "broader_union_target_hits":found,
      "broader_union_target_hit_count":len(found),
      "missing_after_broader_union":sorted(target_codes-union),
      "interpretation":"If broader_union_target_hit_count materially exceeds current_fs hits, Eastmoney capability exists beyond current ETF_FS; this artifact does not authorize production universe expansion."
    }
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False))
if __name__=="__main__": main()
