from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY_DIR = ROOT / "events/research/daily_features"
UNIVERSE = ROOT / "config/market/etf_monitor_universe.json"
OUT = ROOT / "research/backtests/etf_share_flow_stage1_validation.json"
STATUS = ROOT / "data/state/etf_share_flow_research_status.json"

SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_REFERER = "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"
SSE_SQL = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_REFERER = "https://www.szse.cn/market/fund/volume/etf/index.html"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def r4(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def load_price_panel(start: str, end: str) -> pd.DataFrame:
    rows = []
    for p in sorted(DAILY_DIR.glob("*.json")):
        d = p.stem
        if d < start or d > end:
            continue
        payload = load_json(p)
        for x in payload.get("features") or []:
            rows.append({"date": pd.Timestamp(d), "code": str(x.get("code") or ""), "close": x.get("close")})
    f = pd.DataFrame(rows)
    if f.empty:
        raise RuntimeError("No historical ETF daily features")
    f["close"] = pd.to_numeric(f["close"], errors="coerce")
    return f.dropna(subset=["close"]).drop_duplicates(["date", "code"]).sort_values(["date", "code"])


def session(headers):
    s = requests.Session(); s.headers.update(headers); return s


def fetch_sse_by_dates(dates: list[pd.Timestamp], wanted: set[str]) -> list[dict]:
    s = session({"Referer": SSE_REFERER, "User-Agent": "Mozilla/5.0", "Accept": "application/json,text/javascript,*/*;q=0.01"})
    out = []
    for i, ts in enumerate(dates):
        d = ts.strftime("%Y-%m-%d")
        params = {
            "isPagination":"true", "pageHelp.pageSize":"2000", "pageHelp.pageNo":"1",
            "pageHelp.beginPage":"1", "pageHelp.cacheSize":"1", "pageHelp.endPage":"1",
            "sqlId":SSE_SQL, "STAT_DATE":d,
        }
        last = None
        for attempt in range(3):
            try:
                resp = s.get(SSE_URL, params=params, timeout=20); resp.raise_for_status(); data = resp.json();
                rows = data.get("result") or (data.get("pageHelp") or {}).get("data") or []
                for x in rows:
                    code = str(x.get("SEC_CODE") or "").strip()
                    if code not in wanted: continue
                    val = str(x.get("TOT_VOL") or "").replace(",", "").strip()
                    if val:
                        out.append({"date":ts, "code":code, "shares_10k":float(val), "source":"SSE"})
                last = None; break
            except Exception as exc:
                last = exc; time.sleep(0.5 * (attempt + 1))
        if last is not None:
            raise RuntimeError(f"SSE fetch failed {d}: {last}")
        if i % 50 == 0: time.sleep(0.15)
    return out


def split_windows(start: pd.Timestamp, end: pd.Timestamp, span=170):
    cur = start
    while cur <= end:
        e = min(cur + pd.Timedelta(days=span-1), end)
        yield cur, e
        cur = e + pd.Timedelta(days=1)


def fetch_szse_code(code: str, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    s = session({"Referer":SZSE_REFERER,"User-Agent":"Mozilla/5.0","Accept":"application/json,text/javascript,*/*;q=0.01","X-Requested-With":"XMLHttpRequest"})
    out = []
    for ws, we in split_windows(start, end):
        page = 1
        while True:
            params = {
                "SHOWTYPE":"JSON","CATALOGID":"scsj_fund_jjgm","TABKEY":"tab1","jjlb":"ETF",
                "txtDm":code,"txtStart":ws.strftime("%Y-%m-%d"),"txtEnd":we.strftime("%Y-%m-%d"),
                "tab1PAGENO":str(page),"random":str(time.time()),
            }
            last = None
            for attempt in range(3):
                try:
                    resp=s.get(SZSE_URL,params=params,timeout=20,verify=False); resp.raise_for_status(); data=resp.json(); last=None; break
                except Exception as exc:
                    last=exc; time.sleep(0.7*(attempt+1))
            if last is not None: raise RuntimeError(f"SZSE fetch failed {code} {ws.date()}..{we.date()}: {last}")
            block = data[0] if isinstance(data,list) and data else {}
            if block.get("error"): raise RuntimeError(f"SZSE API error {code}: {block.get('error')}")
            rows = block.get("data") or []
            for x in rows:
                val=str(x.get("current_size") or "").replace(",","").strip(); d=str(x.get("size_date") or "").strip()
                if val and d: out.append({"date":pd.Timestamp(d),"code":code,"shares_10k":float(val),"source":"SZSE"})
            pages=int((block.get("metadata") or {}).get("pagecount") or 1)
            if page>=pages: break
            page+=1; time.sleep(0.2)
        time.sleep(0.2)
    return out


def build_panel(price: pd.DataFrame, universe: dict) -> tuple[pd.DataFrame, dict]:
    meta={str(x["code"]):x for x in universe.get("objects") or []}
    sh={c for c,x in meta.items() if str(x.get("thscode","")).endswith(".SH")}
    sz={c for c,x in meta.items() if str(x.get("thscode","")).endswith(".SZ")}
    dates=sorted(price["date"].drop_duplicates().tolist())
    share_rows=fetch_sse_by_dates(dates,sh)
    start,end=min(dates),max(dates)
    for c in sorted(sz): share_rows.extend(fetch_szse_code(c,start,end))
    shares=pd.DataFrame(share_rows).drop_duplicates(["date","code"],keep="last")
    panel=price.merge(shares,on=["date","code"],how="left").sort_values(["code","date"])
    parts=[]
    for code,g in panel.groupby("code",sort=True):
        g=g.copy().sort_values("date")
        close=g["close"]
        g["ret_5d_pct"]=(close/close.shift(5)-1)*100
        g["ret_20d_pct"]=(close/close.shift(20)-1)*100
        for n in [1,3,5]:
            raw=(g["shares_10k"]/g["shares_10k"].shift(n)-1)*100
            g[f"share_change_{n}d_pct_lag1"]=raw.shift(1)
            g[f"neg_share_change_{n}d_pct_lag1"]=-raw.shift(1)
        for h in [1,3,5,10]: g[f"fwd_{h}d_pct"]=(close.shift(-h)/close-1)*100
        parts.append(g)
    return pd.concat(parts,ignore_index=True), {"SSE":len(sh),"SZSE":len(sz),"share_rows":int(len(shares))}


def eval_signal(df: pd.DataFrame, signal: str, label: str, min_n: int, top_k: int) -> dict:
    ics=[]; top1=[]; topk=[]; bottom=[]; dates=0
    for _,g in df[["date","code",signal,label]].dropna().groupby("date"):
        if len(g)<min_n or g[signal].nunique()<2 or g[label].nunique()<2: continue
        ic=g[signal].corr(g[label],method="spearman")
        if pd.isna(ic): continue
        dates+=1; ics.append(float(ic)); o=g.sort_values(signal,ascending=False)
        top1.append(float(o.iloc[0][label])); topk.append(float(o.head(min(top_k,len(o)))[label].mean())); bottom.append(float(o.iloc[-1][label]))
    return {
        "eligible_dates":dates,"mean_rank_ic":r4(np.mean(ics)) if ics else None,"median_rank_ic":r4(np.median(ics)) if ics else None,
        "positive_ic_rate":r4(np.mean(np.asarray(ics)>0)) if ics else None,
        "top1_mean_forward_pct":r4(np.mean(top1)) if top1 else None,"top3_mean_forward_pct":r4(np.mean(topk)) if topk else None,
        "top1_minus_bottom_spread_pct_points":r4(np.mean(np.asarray(top1)-np.asarray(bottom))) if top1 else None,
    }


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("request_path"); args=ap.parse_args()
    req=load_json(ROOT/args.request_path); cfg=load_json(ROOT/req["config_path"])
    price=load_price_panel(req["data_start"],req["data_end"]); panel,coverage=build_panel(price,load_json(UNIVERSE))
    min_n=int(cfg["validation"]["minimum_cross_section_size"]); top_k=int(cfg["validation"]["top_k"])
    folds=[]; agg=defaultdict(lambda:defaultdict(list))
    for fold in req["folds"]:
        sub=panel[panel["date"].between(pd.Timestamp(fold["test_start"]),pd.Timestamp(fold["test_end"]))]
        for h in cfg["horizons_trading_days"]:
            label=f"fwd_{int(h)}d_pct"
            baselines={b:eval_signal(sub,b,label,min_n,top_k) for b in cfg["baselines"]}
            signals={s:eval_signal(sub,s,label,min_n,top_k) for s in cfg["signals"]}
            folds.append({"fold":fold["name"],"horizon_trading_days":int(h),"signals":signals,"baselines":baselines})
            for s,m in signals.items():
                if m["mean_rank_ic"] is not None: agg[s][int(h)].append(float(m["mean_rank_ic"]))
            for b,m in baselines.items():
                if m["mean_rank_ic"] is not None: agg[b][int(h)].append(float(m["mean_rank_ic"]))
    summary={}; passing=[]
    for s in cfg["signals"]:
        hsum={}; inc_count=0; posfold_ok=0
        for h in cfg["horizons_trading_days"]:
            a=agg[s][int(h)]; b5=agg["ret_5d_pct"][int(h)]; b20=agg["ret_20d_pct"][int(h)]
            am=float(np.mean(a)) if a else float("nan"); bm=max(float(np.mean(b5)),float(np.mean(b20))) if b5 and b20 else float("nan")
            pos=sum(x>0 for x in a); inc=math.isfinite(am) and math.isfinite(bm) and am>bm
            if pos>=int(cfg["validation"]["required_positive_ic_folds"]): posfold_ok+=1
            if inc: inc_count+=1
            hsum[str(h)]={"fold_count":len(a),"mean_rank_ic":r4(am),"positive_ic_folds":pos,"best_momentum_mean_rank_ic":r4(bm),"increment_vs_best_momentum":r4(am-bm) if math.isfinite(am) and math.isfinite(bm) else None,"beats_best_momentum":inc}
        passed=inc_count>=int(cfg["validation"]["required_horizons_with_increment"]) and posfold_ok>=int(cfg["validation"]["required_horizons_with_increment"])
        summary[s]={"horizons":hsum,"increment_horizon_count":inc_count,"positive_stable_horizon_count":posfold_ok,"stage1_pass":passed}
        if passed: passing.append(s)
    interpretation="PROMISING_FOR_STAGE2" if passing else "NO_STABLE_INCREMENT_STAGE1"
    payload={
        "schema_version":"1.0","generated_at":now_utc(),"mode":req["mode"],"official_sources":cfg["official_sources"],
        "availability_rule":cfg["availability_rule"],"coverage":coverage,"fold_results":folds,"signal_summary":summary,"passing_signals":passing,
        "research_interpretation":interpretation,"decision_eligible":False,"trade_signal":None,"trial_confirm":None,"portfolio_target":None,"master_override":False,"historical_decision_prohibited":True,
        "interpretation_boundary":"Share-flow statistics are research evidence only. They cannot directly generate risk permission, opportunity status, amount, holding reduction or exit."
    }
    atomic_json_write(OUT,payload)
    status={"generated_at":payload["generated_at"],"status":"PASS","research_interpretation":interpretation,"passing_signals":passing,"coverage":coverage,"decision_eligible":False,"trade_signal":None,"master_override":False}
    atomic_json_write(STATUS,status); print(json.dumps(status,ensure_ascii=False)); return 0

if __name__=="__main__": raise SystemExit(main())
