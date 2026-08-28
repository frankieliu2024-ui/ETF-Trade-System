from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import pandas as pd

import run_market_breadth_margin_stage1_v2 as marginv2

ROOT=Path(os.environ.get("ETF_SYSTEM_ROOT",Path(__file__).resolve().parents[1])).resolve()
base=marginv2.base
REQ=ROOT/"requests/research_backfill/20260825_market_breadth_margin_stage1.json"
OUT=ROOT/"research/backtests/margin_feedback_interaction_validation.json"


def r6(x):
    try:
        v=float(x);return round(v,6) if math.isfinite(v) else None
    except Exception:return None


def summ(vals):
    vals=[float(x) for x in vals if x is not None and math.isfinite(float(x))]
    if not vals:return {"n":0,"mean":None,"median":None,"positive_rate":None}
    return {"n":len(vals),"mean":r6(mean(vals)),"median":r6(median(vals)),"positive_rate":r6(sum(x>0 for x in vals)/len(vals))}


def main():
    req=base.loadj(REQ);cfg=base.loadj(ROOT/req["config_path"]);targets=[x["code"] for x in cfg["targets"]]
    p=base.local_panel(req["data_start"],req["data_end"],targets).sort_values(["code","date"])
    parts=[]
    for c,g in p.groupby("code",sort=True):
        g=g.copy().sort_values("date");g["ret1_lag1"]=(g.close.shift(1)/g.close.shift(2)-1)*100;parts.append(g)
    p=pd.concat(parts,ignore_index=True)
    med=p.groupby("date")["ret1_lag1"].median().rename("ret1_lag1_median")
    p=p.merge(med,left_on="date",right_index=True,how="left");p["relative_feedback_lag1"]=p.ret1_lag1-p.ret1_lag1_median
    td=pd.DatetimeIndex(sorted(p.date.unique()));margin,cov=marginv2.fetch_margin_with_fallback(td,req["data_start"],req["data_end"])
    p=p.merge(margin,left_on="date",right_index=True,how="left")
    rows=[]
    for f in req["folds"]:
        z=p[p.date.between(pd.Timestamp(f["test_start"]),pd.Timestamp(f["test_end"]))].copy()
        for h in (1,3,5):
            label=f"fwd_{h}d";events=defaultdict(list)
            for d,g in z.groupby("date"):
                g=g.dropna(subset=[label,"relative_feedback_lag1","margin_balance_5d_change_lag1"])
                if len(g)<4:continue
                m=float(g.margin_balance_5d_change_lag1.iloc[0]);univ=float(g[label].median())
                for r in g.itertuples(index=False):
                    ex=float(getattr(r,label))-univ;strong=r.relative_feedback_lag1>0
                    regime="EXPANDING" if m>0 else "CONTRACTING"
                    side="STRONG" if strong else "WEAK"
                    events[(regime,side)].append(ex)
            rec={"fold":f["name"],"horizon_trading_days":h}
            for regime in ("CONTRACTING","EXPANDING"):
                s=summ(events[(regime,"STRONG")]);w=summ(events[(regime,"WEAK")])
                rec[regime.lower()]={"strong_feedback":s,"weak_feedback":w,"strong_minus_weak_mean_pct_points":r6((s["mean"]-w["mean"]) if s["mean"] is not None and w["mean"] is not None else None)}
            rows.append(rec)
    passing={}
    for regime in ("contracting","expanding"):
        hs=[]
        for h in (1,3,5):
            cells=[x for x in rows if x["horizon_trading_days"]==h and x[regime]["strong_feedback"]["n"]>=20 and x[regime]["weak_feedback"]["n"]>=20]
            good=sum((x[regime]["strong_minus_weak_mean_pct_points"] or 0)>0.20 for x in cells)
            if len(cells)>=2 and good>=2:hs.append(h)
        passing[regime]=hs
    overall="PASS" if any(len(v)>=2 for v in passing.values()) else "NO_STABLE_INCREMENT"
    out={"schema_version":"1.0","mode":"RESEARCH_ONLY_MARGIN_X_ETF_FEEDBACK_INTERACTION","status":"PASS","margin_coverage":cov,"point_in_time":{"margin":"T margin facts are shifted and first used T+1","etf_feedback":"ETF one-day relative feedback uses closes only through D-1","label":"forward return starts at open D"},"definition":"Within margin-expanding and margin-contracting regimes separately, compare ETFs with positive versus negative prior-day relative feedback against the same-date ETF-universe median forward return.","fold_results":rows,"passing_horizons_by_margin_regime":passing,"research_interpretation":overall,"decision_eligible":False,"trade_signal":None,"trial_confirm":None,"master_override":False,"production_context_integration":False,"boundary":"Interaction research only. A passer may justify conversion review but cannot independently generate risk permission, candidate, amount, sell action or automatic rotation."}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"research_interpretation":overall,"passing_horizons_by_margin_regime":passing,"margin_coverage":cov},ensure_ascii=False));return 0

if __name__=="__main__":raise SystemExit(main())
