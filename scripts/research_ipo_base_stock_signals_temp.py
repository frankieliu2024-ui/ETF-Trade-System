from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = ZoneInfo("Asia/Shanghai")
OUT = ROOT / "research/backtests/ipo_base_stock_signal_validation.json"
STOCKS = {
    "300750": {"name": "宁德时代", "symbol": "300750.SZ", "benchmark": "399006.SZ", "benchmark_name": "创业板指"},
    "601138": {"name": "工业富联", "symbol": "601138.SS", "benchmark": "000001.SS", "benchmark_name": "上证指数"},
}
START = datetime(2020, 1, 1, tzinfo=timezone.utc)
VALIDATION_START = pd.Timestamp("2024-01-01")
HORIZONS = (5, 10)


def sf(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def yahoo_history(symbol: str, end_date) -> pd.DataFrame:
    p1 = int(START.timestamp())
    p2 = int((datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)).timestamp())
    query = urllib.parse.urlencode({"period1": p1, "period2": p2, "interval": "1d", "events": "history", "includeAdjustedClose": "true"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 ETF-Trade-System/2.2.17"})
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.load(r)
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not result:
                raise RuntimeError(str((payload.get("chart") or {}).get("error")))
            ts = result.get("timestamp") or []
            quotes = (((result.get("indicators") or {}).get("quote") or [{}])[0])
            rows = []
            for i, stamp in enumerate(ts):
                close = (quotes.get("close") or [])[i] if i < len(quotes.get("close") or []) else None
                high = (quotes.get("high") or [])[i] if i < len(quotes.get("high") or []) else None
                low = (quotes.get("low") or [])[i] if i < len(quotes.get("low") or []) else None
                open_ = (quotes.get("open") or [])[i] if i < len(quotes.get("open") or []) else None
                volume = (quotes.get("volume") or [])[i] if i < len(quotes.get("volume") or []) else None
                if None in (close, high, low):
                    continue
                d = datetime.fromtimestamp(int(stamp), tz=timezone.utc).astimezone(BEIJING).date()
                rows.append({"date": d.isoformat(), "open": open_, "close": close, "high": high, "low": low, "volume": volume})
            x = pd.DataFrame(rows)
            if len(x) < 500:
                raise RuntimeError(f"insufficient rows={len(x)}")
            x["date"] = pd.to_datetime(x["date"])
            for c in ["open","close","high","low","volume"]:
                x[c] = pd.to_numeric(x[c], errors="coerce")
            return x.dropna(subset=["date","close","high","low"]).sort_values("date").drop_duplicates("date",keep="last").reset_index(drop=True)
        except Exception as exc:
            last = exc
            time.sleep(0.8*(attempt+1))
    raise RuntimeError(f"Yahoo history failed {symbol}: {last}")


def build_frame(stock, bench):
    x = stock.merge(bench[["date","close"]].rename(columns={"close":"benchmark_close"}), on="date", how="inner").sort_values("date").reset_index(drop=True)
    x["ret5"] = x["close"].pct_change(5); x["ret20"] = x["close"].pct_change(20)
    x["ma20"] = x["close"].rolling(20).mean(); x["ma60"] = x["close"].rolling(60).mean()
    x["prior60_high"] = x["high"].shift(1).rolling(60).max(); x["prior60_low"] = x["low"].shift(1).rolling(60).min()
    x["volume_ratio20"] = x["volume"] / x["volume"].shift(1).rolling(20).mean()
    for h in HORIZONS:
        x[f"fwd_stock_{h}"] = x["close"].shift(-h)/x["close"]-1
        x[f"fwd_bench_{h}"] = x["benchmark_close"].shift(-h)/x["benchmark_close"]-1
        x[f"fwd_excess_{h}"] = x[f"fwd_stock_{h}"]-x[f"fwd_bench_{h}"]
    return x


def signal_masks(x):
    vr=x["volume_ratio20"].fillna(0)
    return {
        "TREND_CONTINUATION":("POSITIVE",(x["close"]>x["ma20"])&(x["ma20"]>x["ma60"])&(x["ret20"]>=0.05)),
        "VOLUME_BREAKOUT_60D":("POSITIVE",(x["close"]>=x["prior60_high"]*0.995)&(vr>=1.10)),
        "UPTREND_PULLBACK":("POSITIVE",(x["close"]>x["ma60"])&(x["ma20"]>x["ma60"])&(x["ret5"]<=-0.03)&(x["ret20"]>0)),
        "OVERSOLD_REVERSAL":("POSITIVE",(x["ret5"]<=-0.08)&(x["close"]<x["ma20"])),
        "WEAK_TREND":("NEGATIVE",(x["close"]<x["ma20"])&(x["ma20"]<x["ma60"])&(x["ret20"]<=-0.05)),
        "VOLUME_BREAKDOWN_60D":("NEGATIVE",(x["close"]<=x["prior60_low"]*1.005)&(vr>=1.10)),
    }


def stats(x, mask, h, start, end=None):
    m=mask&(x["date"]>=pd.Timestamp(start))
    if end is not None: m &= x["date"]<pd.Timestamp(end)
    vals=x.loc[m,f"fwd_excess_{h}"].dropna(); stocks=x.loc[m,f"fwd_stock_{h}"].dropna()
    if vals.empty: return {"n":0,"mean_excess_pct":None,"median_excess_pct":None,"positive_excess_rate":None,"mean_stock_return_pct":None}
    return {"n":int(len(vals)),"mean_excess_pct":round(float(vals.mean()*100),4),"median_excess_pct":round(float(vals.median()*100),4),"positive_excess_rate":round(float((vals>0).mean()),4),"mean_stock_return_pct":round(float(stocks.mean()*100),4)}


def evaluate(x):
    candidates=[]; validated=[]; baseline={h:stats(x,pd.Series(True,index=x.index),h,VALIDATION_START) for h in HORIZONS}
    for signal_id,(direction,mask) in signal_masks(x).items():
        train={h:stats(x,mask,h,x["date"].min(),VALIDATION_START) for h in HORIZONS}; val={h:stats(x,mask,h,VALIDATION_START) for h in HORIZONS}; reasons=[]
        for h in HORIZONS:
            v,t,b=val[h],train[h],baseline[h]
            if v["n"]<12: reasons.append(f"H{h}_VALIDATION_N_LT_12"); continue
            if t["n"]<12: reasons.append(f"H{h}_TRAIN_N_LT_12"); continue
            sign=1 if direction=="POSITIVE" else -1; vm=v["mean_excess_pct"]; tm=t["mean_excess_pct"]; med=v["median_excess_pct"]
            edge=None if vm is None or b["mean_excess_pct"] is None else vm-b["mean_excess_pct"]
            hit=v["positive_excess_rate"] if direction=="POSITIVE" else (None if v["positive_excess_rate"] is None else 1-v["positive_excess_rate"])
            if vm is None or sign*vm<=0.50: reasons.append(f"H{h}_MEAN_EXCESS_WEAK")
            if tm is None or sign*tm<=0: reasons.append(f"H{h}_TRAIN_SIGN_NOT_STABLE")
            if med is None or sign*med<=0: reasons.append(f"H{h}_MEDIAN_NOT_ALIGNED")
            if edge is None or sign*edge<=0.35: reasons.append(f"H{h}_NO_INCREMENT_VS_UNCONDITIONAL")
            if hit is None or hit<0.55: reasons.append(f"H{h}_HIT_RATE_LT_55PCT")
        item={"signal_id":signal_id,"direction":direction,"definition_fixed_ex_ante":True,"train":train,"validation":val,"validation_unconditional_baseline":baseline,"decision_eligible":not reasons,"production_context_integration":not reasons,"current_completed_bar_match":bool(mask.iloc[-1]),"rejection_reasons":sorted(set(reasons)),"trade_signal":None}
        candidates.append(item)
        if not reasons: validated.append(item)
    return candidates,validated


def main():
    now=datetime.now(BEIJING); completed_date=now.date()-timedelta(days=1) if now.hour<15 else now.date()
    stock_market=json.loads((ROOT/"data/state/stock_market_context.json").read_text(encoding="utf-8")); outputs=[]; all_validated=[]
    for code,cfg in STOCKS.items():
        stock=yahoo_history(cfg["symbol"],completed_date); bench=yahoo_history(cfg["benchmark"],completed_date); frame=build_frame(stock,bench); candidates,validated=evaluate(frame)
        market=(stock_market.get("objects") or {}).get(code) or {}; last_hist=sf(stock.iloc[-1]["close"]); current_prev=sf(market.get("prev_close")); identity_gap=None if last_hist is None or current_prev in (None,0) else (last_hist/current_prev-1)*100; identity_pass=identity_gap is not None and abs(identity_gap)<=0.50
        if not identity_pass:
            for s in validated: s["decision_eligible"]=False; s["production_context_integration"]=False; s["rejection_reasons"]=sorted(set(s["rejection_reasons"]+["LATEST_HISTORY_IDENTITY_ALIGNMENT_FAILED"]))
            validated=[]
        out={"code":code,"display_name":f"{cfg['name']}（{code}）","benchmark":f"{cfg['benchmark_name']}（{cfg['benchmark']}）","data_source":{"provider":"yahoo_chart_api","symbol":cfg["symbol"],"benchmark_symbol":cfg["benchmark"],"price_field":"raw quote.close","adjusted_close_used":False,"current_identity_reference":"formal stock_market_context"},"sample":{"start":frame["date"].min().date().isoformat(),"end":frame["date"].max().date().isoformat(),"rows":int(len(frame)),"validation_start":"2024-01-01"},"latest_identity_check":{"historical_last_close":last_hist,"formal_current_prev_close":current_prev,"gap_pct":round(identity_gap,4) if identity_gap is not None else None,"status":"PASS" if identity_pass else "FAILED"},"candidate_signals":candidates,"validated_signal_ids":[s["signal_id"] for s in validated],"validated_stock_specific_signal_status":"VALIDATED_RESEARCH_SIGNAL_AVAILABLE" if validated else "NO_STABLE_INCREMENTAL_SIGNAL","decision_eligible":bool(validated),"production_context_integration":bool(validated),"current_completed_bar_matches":[s["signal_id"] for s in validated if s["current_completed_bar_match"]],"automatic_trade":False,"trade_signal":None}
        outputs.append(out); all_validated += [{"code":code,"name":cfg["name"],**s} for s in validated]
    result={"schema_version":"1.0","generated_at_beijing":now.isoformat(timespec="seconds"),"mode":"IPO_BASE_STOCK_SPECIFIC_SIGNAL_PIT_VALIDATION","point_in_time":True,"research_only_execution":True,"automatic_trade":False,"trade_signal":None,"research_design":{"fixed_signal_families":["TREND_CONTINUATION","VOLUME_BREAKOUT_60D","UPTREND_PULLBACK","OVERSOLD_REVERSAL","WEAK_TREND","VOLUME_BREAKDOWN_60D"],"validation_start":"2024-01-01","forward_horizons_trading_days":[5,10],"promotion_gate":"both 5d and 10d: train/validation >=12 signals; validation mean excess >=0.50pp in direction; median aligned; incremental edge vs unconditional >=0.35pp; directional hit rate >=55%; training sign aligned; latest object identity check PASS","multiple_testing_control":"small fixed interpretable family; no parameter grid search; no optimizer; no composite score","boundary":"研究信号只作为执行证据，不生成风险许可、Trial/Confirm、金额、卖出份额或订单。"},"stocks":outputs,"execution_eligible_signal_count":len(all_validated),"execution_eligible_signals":all_validated,"decision_eligible":bool(all_validated),"production_context_integration":bool(all_validated),"conclusion":"存在通过固定PIT门禁的个股专项研究信号，可进入只读研究执行桥。" if all_validated else "两只打新底仓在本轮固定PIT信号族中均未形成足够稳定的增量证据；保留研究结论但不进入执行信号。"}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"ok":True,"execution_eligible_signal_count":len(all_validated),"stocks":[{"code":x["code"],"status":x["validated_stock_specific_signal_status"],"validated":x["validated_signal_ids"],"current_matches":x["current_completed_bar_matches"],"identity":x["latest_identity_check"]} for x in outputs]},ensure_ascii=False)); return 0

if __name__=="__main__": raise SystemExit(main())
