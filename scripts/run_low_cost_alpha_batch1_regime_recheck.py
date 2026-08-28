from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

import run_low_cost_alpha_batch1 as base

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "research/backtests/low_cost_alpha_batch1_regime_recheck.json"


def r6(x):
    try:
        v=float(x); return round(v,6) if math.isfinite(v) else None
    except Exception: return None


def summ(v):
    v=[float(x) for x in v if x is not None and math.isfinite(float(x))]
    if not v:return {"n":0,"mean":None,"median":None,"positive_rate":None}
    return {"n":len(v),"mean":r6(mean(v)),"median":r6(median(v)),"positive_rate":r6(sum(x>0 for x in v)/len(v))}


def main():
    df, dates, fwd = base.load_panel()
    day={}
    for d,g in df.groupby("date"):
        vals=g.set_index("code")["ret"].to_dict()
        if len(vals)>=7:
            day[pd.Timestamp(d)]={"rets":vals,"median":float(np.median(list(vals.values()))),"dispersion":float(np.std(list(vals.values()),ddof=0))}
    ordered=sorted(day)
    res=defaultdict(list); mig=defaultdict(list); diag=defaultdict(lambda:{"weak_days":0,"high_dispersion_days":0,"migration_high_days":0,"migration_lower_days":0})
    for d in ordered:
        fold=base.fold_of(d)
        if not fold:continue
        hist=[day[x] for x in ordered if x<d]
        if len(hist)<45:continue
        weak_cut=float(np.quantile([x["median"] for x in hist],0.25))
        disp_cut=float(np.quantile([x["dispersion"] for x in hist],2/3))
        x=day[d]
        if x["median"]<=weak_cut:
            diag[fold]["weak_days"]+=1
            rs={c:r-x["median"] for c,r in x["rets"].items()}; q=float(np.quantile(list(rs.values()),2/3))
            for c,v in rs.items():
                if v<q:continue
                for h in (3,5,10):
                    rr=fwd(d,c,h); allr=[fwd(d,k,h) for k in rs]; allr=[z for z in allr if z is not None]
                    if rr is not None and allr:res[(fold,h)].append(rr-median(allr))
        high=x["dispersion"]>=disp_cut
        if high:diag[fold]["high_dispersion_days"]+=1
        gd=df[df.date==d].dropna(subset=["mom5_lag1","mom20_lag1"])
        if len(gd)<6:continue
        m5=gd.set_index("code").mom5_lag1.to_dict();m20=gd.set_index("code").mom20_lag1.to_dict();common=set(m5)&set(m20)
        a5=sorted(common,key=lambda c:m5[c],reverse=True);a20=sorted(common,key=lambda c:m20[c],reverse=True)
        lead=[c for c in common if c in set(a5[:3]) and c in set(a20[:3]) and m5[c]>0 and m20[c]>0]
        lag=[c for c in common if c in set(a5[-3:]) and c in set(a20[-3:])]
        bucket="HIGH" if high else "LOWER"
        for h in (10,20):
            aa=[fwd(d,c,h) for c in lead];bb=[fwd(d,c,h) for c in lag];aa=[z for z in aa if z is not None];bb=[z for z in bb if z is not None]
            if aa and bb:
                mig[(fold,h,bucket)].append(mean(aa)-mean(bb));diag[fold]["migration_high_days" if high else "migration_lower_days"]+=1
    res_rows=[];mig_rows=[]
    for f in [x["name"] for x in base.FOLDS]:
        for h in (3,5,10):res_rows.append({"fold":f,"h":h,**summ(res[(f,h)])})
        for h in (10,20):
            hi=summ(mig[(f,h,"HIGH")]);lo=summ(mig[(f,h,"LOWER")])
            mig_rows.append({"fold":f,"h":h,"high_dispersion":hi,"lower_dispersion":lo,"high_minus_lower_mean":r6(hi["mean"]-lo["mean"]) if hi["mean"] is not None and lo["mean"] is not None else None})
    rh=[]
    for h in (3,5,10):
        cells=[x for x in res_rows if x["h"]==h and x["n"]>=10]
        if len(cells)==3 and sum((x["mean"] or 0)>0.002 for x in cells)>=2 and mean([x["mean"] for x in cells])>0.002:rh.append(h)
    mh=[]
    for h in (10,20):
        cells=[x for x in mig_rows if x["h"]==h and x["high_dispersion"]["n"]>=8 and x["lower_dispersion"]["n"]>=8]
        if len(cells)>=2 and sum((x["high_dispersion"]["mean"] or 0)>0.002 for x in cells)>=2 and sum((x["high_minus_lower_mean"] or 0)>0 for x in cells)>=2:mh.append(h)
    res_status="PASS" if len(rh)>=2 else ("INSUFFICIENT_EVENTS" if sum(x["n"] for x in res_rows)<30 else "NO_STABLE_INCREMENT")
    mig_status="PASS" if mh else ("INSUFFICIENT_EVENTS" if sum(x["high_dispersion"]["n"] for x in mig_rows)<20 else "NO_STABLE_INCREMENT")
    out={"mode":"RESEARCH_ONLY_LOW_COST_ALPHA_REGIME_RECHECK","point_in_time":"Weak/dispersion thresholds are expanding quantiles computed only from dates strictly before each signal date.","diagnostics":dict(diag),"weak_market_resilience":{"status":res_status,"passing_horizons":rh,"fold_results":res_rows},"dispersion_conditioned_migration":{"status":mig_status,"passing_horizons":mh,"fold_results":mig_rows},"decision_eligible":False,"trade_signal":None,"master_override":False,"production_context_integration":False}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"weak_market_resilience":res_status,"dispersion_conditioned_migration":mig_status,"diagnostics":dict(diag)},ensure_ascii=False))
    return 0

if __name__=="__main__":raise SystemExit(main())
