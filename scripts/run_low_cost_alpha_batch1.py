from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

import run_cross_market_tech_stage1 as cross

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY = ROOT / "events/research/daily_features"
OUT = ROOT / "research/backtests/low_cost_alpha_batch1_validation.json"
BJ = timezone(timedelta(hours=8), name="Asia/Shanghai")
FOLDS = [
    {"name": "fold1", "start": "2024-04-01", "end": "2024-12-31"},
    {"name": "fold2", "start": "2025-01-01", "end": "2025-12-31"},
    {"name": "fold3", "start": "2026-01-01", "end": "2026-08-21"},
]
TECH = ["561980", "588000", "159781", "515880"]


def r6(x):
    if x is None:
        return None
    try:
        v = float(x)
        return round(v, 6) if math.isfinite(v) else None
    except Exception:
        return None


def summary(vals):
    vals = [float(x) for x in vals if x is not None and math.isfinite(float(x))]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "positive_rate": None}
    return {"n": len(vals), "mean": r6(mean(vals)), "median": r6(median(vals)), "positive_rate": r6(sum(x > 0 for x in vals) / len(vals))}


def load_panel():
    rows = []
    for p in sorted(DAILY.glob("*.json")):
        d = p.stem
        if d < "2024-01-01" or d > "2026-08-21":
            continue
        o = json.loads(p.read_text(encoding="utf-8"))
        if o.get("market_phase") not in ("CLOSED", None) and not o.get("historical_backfill"):
            continue
        for x in o.get("features") or []:
            try:
                row = {
                    "date": pd.Timestamp(d), "code": str(x["code"]), "name": x.get("name"),
                    "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]), "close": float(x["close"]),
                    "volume": float(x["volume"]), "ret": float(x.get("close_return_pct")),
                }
            except Exception:
                continue
            if row["open"] > 0 and row["close"] > 0 and row["volume"] >= 0:
                rows.append(row)
    df = pd.DataFrame(rows).drop_duplicates(["date", "code"]).sort_values(["code", "date"])
    if df.empty:
        raise RuntimeError("no daily feature history")
    parts = []
    for code, g in df.groupby("code", sort=True):
        g = g.copy().sort_values("date")
        g["prev_close"] = g["close"].shift(1)
        g["gap_pct"] = (g["open"] / g["prev_close"] - 1.0) * 100.0
        g["mom5_lag1"] = (g["close"].shift(1) / g["close"].shift(6) - 1.0) * 100.0
        g["mom20_lag1"] = (g["close"].shift(1) / g["close"].shift(21) - 1.0) * 100.0
        g["vol20_lag1"] = g["volume"].shift(1).rolling(20, min_periods=15).mean()
        g["volume_ratio"] = g["volume"] / g["vol20_lag1"]
        rng = g["high"] - g["low"]
        g["close_location"] = np.where(rng > 1e-12, (g["close"] - g["low"]) / rng, 0.5)
        parts.append(g)
    df = pd.concat(parts, ignore_index=True).sort_values(["date", "code"])
    dates = sorted(df["date"].unique())
    date_pos = {pd.Timestamp(d): i for i, d in enumerate(dates)}
    by = {(pd.Timestamp(r.date), r.code): r for r in df.itertuples(index=False)}

    def fwd(d, code, h):
        d = pd.Timestamp(d); i = date_pos.get(d)
        if i is None or i + 1 >= len(dates) or i + h >= len(dates):
            return None
        a, b = pd.Timestamp(dates[i + 1]), pd.Timestamp(dates[i + h])
        ra, rb = by.get((a, code)), by.get((b, code))
        if ra is None or rb is None or ra.open <= 0:
            return None
        return rb.close / ra.open - 1.0
    return df, dates, fwd


def fold_of(d):
    s = pd.Timestamp(d).date().isoformat()
    for f in FOLDS:
        if f["start"] <= s <= f["end"]:
            return f["name"]
    return None


def opening_residual(df):
    local = df[df.code.isin(TECH)][["date", "code", "open", "close", "gap_pct", "mom5_lag1", "mom20_lag1"]].copy()
    sox = cross.fetch_yahoo_history("^SOX", "2024-01-01", "2026-08-21")
    ndx = cross.fetch_yahoo_history("^NDX", "2024-01-01", "2026-08-21")
    ext = cross.attach_external(local[["date", "code", "open", "close"]], sox, ndx)
    local = local.merge(ext[["date", "code", "us_tech_equal_1d", "sox_1d", "ndx_1d", "us_source_date"]], on=["date", "code"], how="left")
    local["open_to_close_pct"] = (local.close / local.open - 1.0) * 100.0
    rows, fold_results = [], []
    for f in FOLDS:
        start, end = pd.Timestamp(f["start"]), pd.Timestamp(f["end"])
        for code in TECH:
            train = local[(local.code == code) & (local.date < start)][["us_tech_equal_1d", "gap_pct"]].dropna()
            test = local[(local.code == code) & local.date.between(start, end)].copy()
            if len(train) < 45:
                continue
            X = np.column_stack([np.ones(len(train)), train.us_tech_equal_1d.to_numpy(float)])
            y = train.gap_pct.to_numpy(float)
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            test = test.dropna(subset=["us_tech_equal_1d", "gap_pct", "open_to_close_pct"])
            if test.empty:
                continue
            test["expected_gap"] = beta[0] + beta[1] * test.us_tech_equal_1d
            test["underpricing_signal"] = -(test.gap_pct - test.expected_gap)
            q1, q2 = test.underpricing_signal.quantile([1/3, 2/3])
            hi = test[test.underpricing_signal >= q2].open_to_close_pct
            lo = test[test.underpricing_signal <= q1].open_to_close_pct
            spread = float(hi.mean() - lo.mean()) if len(hi) and len(lo) else None
            corr = test[["underpricing_signal", "open_to_close_pct"]].corr(method="spearman").iloc[0,1] if len(test) >= 20 else np.nan
            rec = {"fold": f["name"], "code": code, "train_n": len(train), "test_n": len(test), "beta_us_to_gap": r6(beta[1]), "spearman": r6(corr), "top_bottom_open_to_close_spread_pct_points": r6(spread)}
            fold_results.append(rec); rows.append(rec)
    valid = [x for x in rows if x["spearman"] is not None and x["top_bottom_open_to_close_spread_pct_points"] is not None]
    positive_cells = sum(x["spearman"] > 0 and x["top_bottom_open_to_close_spread_pct_points"] > 0 for x in valid)
    stable = defaultdict(int)
    for x in valid:
        if x["spearman"] > 0 and x["top_bottom_open_to_close_spread_pct_points"] > 0:
            stable[x["code"]] += 1
    passers = sorted(k for k,v in stable.items() if v >= 2)
    return {"status": "PASS" if len(passers) >= 2 and positive_cells >= 7 else "NO_STABLE_INCREMENT", "signal": "underpricing = -(actual_open_gap - expanding-window expected gap from completed US tech session)", "fold_results": fold_results, "stable_etfs": passers, "positive_cells": positive_cells, "pit": "Each fold estimates normal US->A-share gap only from dates before that fold; US session must complete before A-share open."}


def resilience_and_dispersion(df, dates, fwd):
    day = {}
    for d, g in df.groupby("date"):
        vals = g.set_index("code")["ret"].to_dict()
        if len(vals) < 7: continue
        day[pd.Timestamp(d)] = {"rets": vals, "median": float(np.median(list(vals.values()))), "positive_share": sum(v>0 for v in vals.values())/len(vals), "dispersion": float(np.std(list(vals.values()), ddof=0))}
    res_fold, mig_fold = [], []
    res_events = defaultdict(list); mig_events = defaultdict(list)
    for f in FOLDS:
        start,end=pd.Timestamp(f["start"]),pd.Timestamp(f["end"])
        hist=[x["median"] for d,x in day.items() if d<start]
        dhist=[x["dispersion"] for d,x in day.items() if d<start]
        if len(hist)<45 or len(dhist)<45: continue
        weak_cut=float(np.quantile(hist,0.25)); disp_cut=float(np.quantile(dhist,2/3))
        for d,x in day.items():
            if not (start<=d<=end): continue
            if x["median"]<=weak_cut and x["positive_share"]<=0.40:
                rs={c:r-x["median"] for c,r in x["rets"].items()}
                if len(rs)>=6:
                    q=np.quantile(list(rs.values()),2/3)
                    for c,v in rs.items():
                        if v>=q:
                            for h in (3,5,10):
                                rr=fwd(d,c,h)
                                if rr is None: continue
                                base=[fwd(d,k,h) for k in rs]; base=[z for z in base if z is not None]
                                if base: res_events[(f["name"],h)].append(rr-median(base))
            # reuse active-return leader/laggard definition, but condition by contemporaneous return dispersion
            # momentum is known at close D and allocation begins next open.
            gd=df[df.date==d].dropna(subset=["mom5_lag1","mom20_lag1"])
            if len(gd)>=6:
                m5=gd.set_index("code").mom5_lag1.to_dict(); m20=gd.set_index("code").mom20_lag1.to_dict(); common=set(m5)&set(m20)
                r5=sorted(common,key=lambda c:m5[c],reverse=True); r20=sorted(common,key=lambda c:m20[c],reverse=True)
                lead=[c for c in common if c in set(r5[:3]) and c in set(r20[:3]) and m5[c]>0 and m20[c]>0]
                lag=[c for c in common if c in set(r5[-3:]) and c in set(r20[-3:])]
                bucket="HIGH" if x["dispersion"]>=disp_cut else "LOWER"
                for h in (10,20):
                    a=[fwd(d,c,h) for c in lead]; b=[fwd(d,c,h) for c in lag]; a=[z for z in a if z is not None]; b=[z for z in b if z is not None]
                    if a and b: mig_events[(f["name"],h,bucket)].append(mean(a)-mean(b))
        for h in (3,5,10): res_fold.append({"fold":f["name"],"h":h,**summary(res_events[(f["name"],h)])})
        for h in (10,20):
            hi=summary(mig_events[(f["name"],h,"HIGH")]); lo=summary(mig_events[(f["name"],h,"LOWER")])
            mig_fold.append({"fold":f["name"],"h":h,"high_dispersion":hi,"lower_dispersion":lo,"high_minus_lower_mean":r6((hi["mean"]-lo["mean"]) if hi["mean"] is not None and lo["mean"] is not None else None)})
    res_pass_h=[]
    for h in (3,5,10):
        cells=[x for x in res_fold if x["h"]==h and x["n"]>=10]
        if len(cells)>=2 and sum((x["mean"] or 0)>0 for x in cells)>=2 and mean([x["mean"] for x in cells])>0.002: res_pass_h.append(h)
    mig_pass_h=[]
    for h in (10,20):
        cells=[x for x in mig_fold if x["h"]==h and x["high_dispersion"]["n"]>=8]
        positive=sum((x["high_dispersion"]["mean"] or 0)>0.002 for x in cells)
        better=sum((x["high_minus_lower_mean"] or 0)>0 for x in cells)
        if len(cells)>=2 and positive>=2 and better>=2: mig_pass_h.append(h)
    return ({"status":"PASS" if len(res_pass_h)>=2 else "NO_STABLE_INCREMENT","passing_horizons":res_pass_h,"fold_results":res_fold,"definition":"On historically weak days (fold-specific prior-data bottom-quartile ETF median return and <=40% advancers), test top-third ETF excess resilience from next open."},
            {"status":"PASS" if len(mig_pass_h)>=1 else "NO_STABLE_INCREMENT","passing_horizons":mig_pass_h,"fold_results":mig_fold,"definition":"Condition the existing leader-vs-laggard next-open migration spread on fold-specific prior-data top-third same-day ETF return dispersion; no mechanical rotation."})


def effort_result(df, dates, fwd):
    events=defaultdict(list); results=[]
    for f in FOLDS:
        start,end=pd.Timestamp(f["start"]),pd.Timestamp(f["end"])
        train=df[df.date<start].dropna(subset=["volume_ratio"])
        if len(train)<100: continue
        high=float(train.volume_ratio.quantile(2/3)); low=float(train.volume_ratio.quantile(1/3))
        test=df[df.date.between(start,end)].dropna(subset=["volume_ratio","close_location"])
        for r in test.itertuples(index=False):
            kind=None
            if r.volume_ratio>=high and r.ret>0 and r.close_location>=0.80: kind="EFFICIENT_DEMAND"
            elif r.volume_ratio>=high and r.close_location<=0.40: kind="HIGH_EFFORT_WEAK_RESULT"
            elif r.volume_ratio<=low and r.ret<0 and r.close_location>=0.55: kind="SELLING_EXHAUSTION_CANDIDATE"
            if not kind: continue
            for h in (1,3,5):
                rr=fwd(r.date,r.code,h)
                if rr is None: continue
                base=[fwd(r.date,c,h) for c in test[test.date==r.date].code.unique()]; base=[z for z in base if z is not None]
                if base: events[(f["name"],kind,h)].append(rr-median(base))
        for kind in ("EFFICIENT_DEMAND","HIGH_EFFORT_WEAK_RESULT","SELLING_EXHAUSTION_CANDIDATE"):
            for h in (1,3,5): results.append({"fold":f["name"],"pattern":kind,"h":h,**summary(events[(f["name"],kind,h)])})
    # Expected signs: demand/exhaustion positive, weak-result negative.
    pattern_pass={}
    for kind, sign in (("EFFICIENT_DEMAND",1),("HIGH_EFFORT_WEAK_RESULT",-1),("SELLING_EXHAUSTION_CANDIDATE",1)):
        hs=[]
        for h in (1,3,5):
            cells=[x for x in results if x["pattern"]==kind and x["h"]==h and x["n"]>=10]
            good=sum(sign*(x["mean"] or 0)>0.0015 for x in cells)
            if len(cells)>=2 and good>=2: hs.append(h)
        pattern_pass[kind]=hs
    return {"status":"PASS" if any(len(v)>=2 for v in pattern_pass.values()) else "NO_STABLE_INCREMENT","passing_horizons_by_pattern":pattern_pass,"fold_results":results,"definition":"Daily OHLCV only: volume effort versus close-location/result, with fold thresholds learned only from prior dates. Signal at close D, execution evaluation from next open."}


def main():
    df,dates,fwd=load_panel()
    opening=opening_residual(df)
    resilience,dispersion=resilience_and_dispersion(df,dates,fwd)
    effort=effort_result(df,dates,fwd)
    results={"opening_pricing_residual":opening,"weak_market_resilience":resilience,"dispersion_conditioned_migration":dispersion,"effort_result":effort}
    passing=[k for k,v in results.items() if v["status"]=="PASS"]
    out={
        "schema_version":"1.0","generated_at_beijing":datetime.now(BJ).isoformat(timespec="seconds"),
        "mode":"RESEARCH_ONLY_LOW_COST_ALPHA_BATCH1","status":"PASS","data_quality":{"daily_feature_days":len(dates),"first_date":str(pd.Timestamp(dates[0]).date()),"last_date":str(pd.Timestamp(dates[-1]).date()),"no_new_bulk_history_download":True},
        "hypotheses":results,"passing_hypotheses":passing,"research_interpretation":"PROMISING_LOW_COST_ALPHA_EVIDENCE_REQUIRES_SEPARATE_CONVERSION_REVIEW" if passing else "NO_STABLE_INCREMENT_BATCH1",
        "decision_eligible":False,"trade_signal":None,"trial_confirm":None,"master_override":False,"production_context_integration":False,
        "boundary":"Each hypothesis is independently accepted/rejected. Results are research-only and cannot create risk permission, Trial/Confirm, amount, sell action, hidden score or automatic rotation. Any passer requires separate formal conversion review."
    }
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":out["status"],"passing_hypotheses":passing,"results":{k:v["status"] for k,v in results.items()}},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
