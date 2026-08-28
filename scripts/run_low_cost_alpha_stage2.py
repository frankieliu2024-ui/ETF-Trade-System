from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

import run_low_cost_alpha_batch1 as b1
import run_cross_market_tech_stage1 as cross
import run_market_breadth_margin_stage1_v2 as marginv2

ROOT=Path(os.environ.get("ETF_SYSTEM_ROOT",Path(__file__).resolve().parents[1])).resolve()
OUT=ROOT/"research/backtests/low_cost_alpha_stage2_validation.json"
REQ=ROOT/"requests/research_backfill/20260825_market_breadth_margin_stage1.json"


def r6(x):
    try:
        v=float(x);return round(v,6) if math.isfinite(v) else None
    except Exception:return None


def summ(v):
    v=[float(x) for x in v if x is not None and math.isfinite(float(x))]
    if not v:return {"n":0,"mean":None,"median":None,"positive_rate":None}
    return {"n":len(v),"mean":r6(mean(v)),"median":r6(median(v)),"positive_rate":r6(sum(x>0 for x in v)/len(v))}


def rolling_regime(df,fwd):
    day={}
    for d,g in df.groupby("date"):
        vals=g.set_index("code")["ret"].to_dict()
        if len(vals)>=7:day[pd.Timestamp(d)]={"rets":vals,"median":float(np.median(list(vals.values()))),"dispersion":float(np.std(list(vals.values()),ddof=0))}
    ordered=sorted(day);res=defaultdict(list);mig=defaultdict(list);diag=defaultdict(lambda:{"weak_days":0,"high_dispersion_days":0})
    for i,d in enumerate(ordered):
        fold=b1.fold_of(d)
        if not fold or i<60:continue
        hist=[day[x] for x in ordered[max(0,i-60):i]]
        weak_cut=float(np.quantile([x["median"] for x in hist],0.25));disp_cut=float(np.quantile([x["dispersion"] for x in hist],2/3));x=day[d]
        if x["median"]<=weak_cut:
            diag[fold]["weak_days"]+=1;rs={c:r-x["median"] for c,r in x["rets"].items()};q=float(np.quantile(list(rs.values()),2/3))
            for c,v in rs.items():
                if v<q:continue
                for h in (3,5,10):
                    rr=fwd(d,c,h);base=[fwd(d,k,h) for k in rs];base=[z for z in base if z is not None]
                    if rr is not None and base:res[(fold,h)].append(rr-median(base))
        high=x["dispersion"]>=disp_cut
        if high:diag[fold]["high_dispersion_days"]+=1
        gd=df[df.date==d].dropna(subset=["mom5_lag1","mom20_lag1"])
        if len(gd)<6:continue
        m5=gd.set_index("code").mom5_lag1.to_dict();m20=gd.set_index("code").mom20_lag1.to_dict();cs=set(m5)&set(m20)
        r5=sorted(cs,key=lambda c:m5[c],reverse=True);r20=sorted(cs,key=lambda c:m20[c],reverse=True)
        lead=[c for c in cs if c in set(r5[:3]) and c in set(r20[:3]) and m5[c]>0 and m20[c]>0];lag=[c for c in cs if c in set(r5[-3:]) and c in set(r20[-3:])]
        for h in (10,20):
            a=[fwd(d,c,h) for c in lead];bb=[fwd(d,c,h) for c in lag];a=[z for z in a if z is not None];bb=[z for z in bb if z is not None]
            if a and bb:mig[(fold,h,"HIGH" if high else "LOWER")].append(mean(a)-mean(bb))
    rr=[];mm=[]
    for f in [x["name"] for x in b1.FOLDS]:
        for h in (3,5,10):rr.append({"fold":f,"h":h,**summ(res[(f,h)])})
        for h in (10,20):
            a=summ(mig[(f,h,"HIGH")]);z=summ(mig[(f,h,"LOWER")]);mm.append({"fold":f,"h":h,"high":a,"lower":z,"high_minus_lower":r6(a["mean"]-z["mean"]) if a["mean"] is not None and z["mean"] is not None else None})
    rh=[];mh=[]
    for h in (3,5,10):
        cells=[x for x in rr if x["h"]==h and x["n"]>=10]
        if len(cells)==3 and sum((x["mean"] or 0)>0.002 for x in cells)>=2 and mean([x["mean"] for x in cells])>0.002:rh.append(h)
    for h in (10,20):
        cells=[x for x in mm if x["h"]==h and x["high"]["n"]>=8 and x["lower"]["n"]>=8]
        if len(cells)>=2 and sum((x["high"]["mean"] or 0)>0.002 for x in cells)>=2 and sum((x["high_minus_lower"] or 0)>0 for x in cells)>=2:mh.append(h)
    return {"weak_market_resilience":{"status":"PASS" if len(rh)>=2 else "NO_STABLE_INCREMENT","passing_horizons":rh,"fold_results":rr},"dispersion_conditioned_migration":{"status":"PASS" if mh else "NO_STABLE_INCREMENT","passing_horizons":mh,"fold_results":mm},"diagnostics":dict(diag),"pit":"60-trading-day rolling thresholds use only dates before signal D."}


def opening_target(df):
    code="561980";g=df[df.code==code][["date","code","open","close","gap_pct"]].copy().sort_values("date")
    sox=cross.fetch_yahoo_history("^SOX","2024-01-01","2026-08-21");ndx=cross.fetch_yahoo_history("^NDX","2024-01-01","2026-08-21")
    ext=cross.attach_external(g[["date","code","open","close"]],sox,ndx);g=g.merge(ext[["date","code","us_tech_equal_1d"]],on=["date","code"],how="left")
    ds=list(g.date);pos={d:i for i,d in enumerate(ds)};rows=[]
    for f in b1.FOLDS:
        start,end=pd.Timestamp(f["start"]),pd.Timestamp(f["end"]);train=g[g.date<start].dropna(subset=["us_tech_equal_1d","gap_pct"]);test=g[g.date.between(start,end)].dropna(subset=["us_tech_equal_1d","gap_pct"])
        if len(train)<45:continue
        X=np.column_stack([np.ones(len(train)),train.us_tech_equal_1d]);beta,*_=np.linalg.lstsq(X,train.gap_pct.to_numpy(float),rcond=None);test=test.copy();test["signal"]=-(test.gap_pct-(beta[0]+beta[1]*test.us_tech_equal_1d))
        for h in (0,1,3):
            vals=[]
            for r in test.itertuples(index=False):
                i=pos.get(r.date)
                if i is None:continue
                j=i if h==0 else i+h
                if j>=len(ds):continue
                exit_row=g[g.date==ds[j]]
                if exit_row.empty:continue
                ret=float(exit_row.close.iloc[0]/r.open-1.0)*100.0;vals.append((float(r.signal),ret))
            if len(vals)<30:continue
            a=pd.DataFrame(vals,columns=["s","y"]);q1,q2=a.s.quantile([1/3,2/3]);spread=float(a[a.s>=q2].y.mean()-a[a.s<=q1].y.mean());rho=float(a.corr(method="spearman").iloc[0,1]);rows.append({"fold":f["name"],"horizon":"same_day" if h==0 else f"{h}d_from_open","n":len(a),"spearman":r6(rho),"top_bottom_spread_pct_points":r6(spread)})
    hs=[]
    for h in ("same_day","1d_from_open","3d_from_open"):
        cells=[x for x in rows if x["horizon"]==h]
        if len(cells)==3 and sum((x["spearman"] or 0)>0 and (x["top_bottom_spread_pct_points"] or 0)>0 for x in cells)>=2:hs.append(h)
    return {"status":"PASS" if len(hs)>=2 else "NO_STABLE_INCREMENT","code":code,"passing_horizons":hs,"fold_results":rows,"pit":"Normal US-to-open coefficient estimated only before each fold; signal available at A-share open."}


def effort_controls(df,fwd):
    rows=[]
    for f in b1.FOLDS:
        start,end=pd.Timestamp(f["start"]),pd.Timestamp(f["end"]);train=df[df.date<start].dropna(subset=["volume_ratio"]);test=df[df.date.between(start,end)].dropna(subset=["volume_ratio","close_location"])
        hi=float(train.volume_ratio.quantile(2/3));lo=float(train.volume_ratio.quantile(1/3))
        for h in (1,3,5):
            buckets=defaultdict(list)
            for r in test.itertuples(index=False):
                rr=fwd(r.date,r.code,h)
                if rr is None:continue
                allr=[fwd(r.date,c,h) for c in test[test.date==r.date].code.unique()];allr=[z for z in allr if z is not None]
                if not allr:continue
                ex=rr-median(allr)
                if r.volume_ratio>=hi and r.ret>0:
                    buckets["DEMAND_HIGH_CLOSE" if r.close_location>=0.80 else "DEMAND_CONTROL"].append(ex)
                if r.volume_ratio<=lo and r.ret<0:
                    buckets["EXHAUSTION_OFF_LOW" if r.close_location>=0.55 else "EXHAUSTION_CONTROL"].append(ex)
            for pair in (("DEMAND_HIGH_CLOSE","DEMAND_CONTROL"),("EXHAUSTION_OFF_LOW","EXHAUSTION_CONTROL")):
                a=summ(buckets[pair[0]]);z=summ(buckets[pair[1]]);rows.append({"fold":f["name"],"h":h,"pattern":pair[0],"pattern_stats":a,"control_stats":z,"incremental_mean":r6(a["mean"]-z["mean"]) if a["mean"] is not None and z["mean"] is not None else None})
    passing=defaultdict(list)
    for pat in ("DEMAND_HIGH_CLOSE","EXHAUSTION_OFF_LOW"):
        for h in (1,3,5):
            cells=[x for x in rows if x["pattern"]==pat and x["h"]==h and x["pattern_stats"]["n"]>=20 and x["control_stats"]["n"]>=20]
            if len(cells)>=2 and sum((x["incremental_mean"] or 0)>0.001 for x in cells)>=2:passing[pat].append(h)
    return {"status":"PASS" if any(len(v)>=2 for v in passing.values()) else "NO_STABLE_INCREMENT","passing_horizons_by_pattern":dict(passing),"fold_results":rows,"control":"Compare close-location pattern against same-sign/same-volume-regime days without the qualifying close-location."}


def margin_by_etf():
    req=marginv2.base.loadj(REQ);cfg=marginv2.base.loadj(ROOT/req["config_path"]);targets=[x["code"] for x in cfg["targets"]];p=marginv2.base.local_panel(req["data_start"],req["data_end"],targets).sort_values(["code","date"])
    parts=[]
    for c,g in p.groupby("code"):
        g=g.copy();g["ret1_lag1"]=(g.close.shift(1)/g.close.shift(2)-1)*100;parts.append(g)
    p=pd.concat(parts,ignore_index=True);med=p.groupby("date").ret1_lag1.median().rename("med");p=p.merge(med,left_on="date",right_index=True,how="left");p["rel"]=p.ret1_lag1-p.med
    td=pd.DatetimeIndex(sorted(p.date.unique()));m,cov=marginv2.fetch_margin_with_fallback(td,req["data_start"],req["data_end"]);p=p.merge(m,left_on="date",right_index=True,how="left")
    rows=[]
    for f in req["folds"]:
        z=p[p.date.between(pd.Timestamp(f["test_start"]),pd.Timestamp(f["test_end"]))]
        for code in targets:
            e=z[(z.code==code)&(z.margin_balance_5d_change_lag1>0)].dropna(subset=["rel"])
            for h in (3,5):
                lab=f"fwd_{h}d";a=e[e.rel>0][lab].dropna().tolist();bb=e[e.rel<=0][lab].dropna().tolist();rows.append({"fold":f["name"],"code":code,"h":h,"strong":summ(a),"weak":summ(bb),"strong_minus_weak":r6(mean(a)-mean(bb)) if a and bb else None})
    stable=[]
    for code in targets:
        good_h=0
        for h in (3,5):
            cells=[x for x in rows if x["code"]==code and x["h"]==h and x["strong"]["n"]>=10 and x["weak"]["n"]>=10]
            if len(cells)>=2 and sum((x["strong_minus_weak"] or 0)>0.20 for x in cells)>=2:good_h+=1
        if good_h>=1:stable.append(code)
    return {"status":"PASS" if len(stable)>=3 else "NO_STABLE_INCREMENT","stable_etfs":stable,"fold_results":rows,"margin_coverage":cov,"scope":"Only margin-expanding regime, because Stage1 interaction passed 3d/5d there; require effect not to be driven by too few ETFs."}


def main():
    df,dates,fwd=b1.load_panel();out={"mode":"RESEARCH_ONLY_LOW_COST_ALPHA_STAGE2","rolling_regime_recheck":rolling_regime(df,fwd),"opening_residual_561980":opening_target(df),"effort_result_incremental_control":effort_controls(df,fwd),"margin_expansion_feedback_by_etf":margin_by_etf(),"decision_eligible":False,"trade_signal":None,"master_override":False,"production_context_integration":False,"boundary":"Focused robustness only; no result is automatically converted into formal execution evidence."}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v.get("status") for k,v in out.items() if isinstance(v,dict) and "status" in v},ensure_ascii=False));return 0
if __name__=="__main__":raise SystemExit(main())
