#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import re
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "research" / "backtests" / "revalidate_section6_baselines.py"
SIGNAL_BASE = ROOT / "research" / "backtests" / "analyze_ashare_ancestral_intraday_history.py"
UNIVERSE = ROOT / "config" / "market" / "etf_monitor_universe.json"
OUT = ROOT / "research" / "reports" / "generated" / "section6_revalidation"
HORIZONS = (1, 3, 5, 10)

s1 = importlib.util.spec_from_file_location("section6_base", BASELINE)
mod = importlib.util.module_from_spec(s1); assert s1 and s1.loader; s1.loader.exec_module(mod)
s2 = importlib.util.spec_from_file_location("intraday_signal_base", SIGNAL_BASE)
sigmod = importlib.util.module_from_spec(s2); assert s2 and s2.loader; s2.loader.exec_module(sigmod)


def f(x):
    try:
        v = float(x); return v if math.isfinite(v) else None
    except Exception:
        return None


def sina_symbol(code):
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


def universe():
    obj = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    return [(str(x.get("code")), str(x.get("name") or x.get("code"))) for x in (obj.get("objects") or []) if len(str(x.get("code") or "")) == 6]


def fetch_15m(code):
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
    params = {"symbol": sina_symbol(code), "scale": "15", "ma": "no", "datalen": "1023"}
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={
        "User-Agent":"Mozilla/5.0 ETF-Trade-System research-only", "Referer":"https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        text = resp.read().decode("utf-8", "replace")
    m = re.search(r"(\[\s*\{.*\}\s*\])", text, re.S)
    if not m: raise RuntimeError("no JSON array")
    arr = json.loads(m.group(1)); bars=[]
    for x in arr:
        dt = datetime.fromisoformat(str(x.get("day")))
        bars.append({"dt":dt,"date":dt.date().isoformat(),"time":dt.strftime("%H:%M"),
                     "open":f(x.get("open")),"close":f(x.get("close")),"high":f(x.get("high")),"low":f(x.get("low")),
                     "volume":f(x.get("volume")),"amount":f(x.get("amount"))})
    if not bars: raise RuntimeError("no usable bars")
    return bars


def derive_daily(code, name, by_d):
    dates=sorted(by_d); rows=[]; prev=None
    for d in dates:
        xs=sorted(by_d[d], key=lambda z:z["dt"])
        if not xs: continue
        o=xs[0]["open"]; c=xs[-1]["close"]
        hs=[x["high"] for x in xs if x["high"] is not None]; ls=[x["low"] for x in xs if x["low"] is not None]
        amount=sum(x["amount"] or 0 for x in xs) if any(x["amount"] is not None for x in xs) else None
        row={"date":d,"code":code,"name":name,"open":o,"high":max(hs) if hs else None,"low":min(ls) if ls else None,
             "close":c,"prev_close":prev,"amount":amount,"change_pct":mod.pct(c,prev) if prev not in (None,0) else None}
        if row["high"] is not None and row["low"] is not None and row["high"] != row["low"] and c is not None:
            row["clv"]=(c-row["low"])/(row["high"]-row["low"])
        else: row["clv"]=None
        rows.append(row); prev=c
    return rows


def prior20_high(rows):
    out={}
    for i,r in enumerate(rows):
        hs=[q["high"] for q in rows[max(0,i-20):i] if q.get("high") is not None]
        out[r["date"]]=max(hs) if len(hs)>=20 else None
    return out


def inject_breakout_60(rows, by_d, ph):
    rmap={r["date"]:r for r in rows}
    for d,r in rmap.items():
        r["signals"]["breakout_hold_60m"]=False; r["signals"]["breakout_fail_60m"]=False
        level=ph.get(d); xs=sorted(by_d.get(d,[]), key=lambda z:z["dt"])
        if level is None: continue
        first=None
        for i,x in enumerate(xs):
            if x.get("close") is not None and x["close"]>level:
                first=i; break
        if first is not None and first+4 < len(xs):
            r["signals"]["breakout_hold_60m"]=xs[first+4]["close"]>level
            r["signals"]["breakout_fail_60m"]=xs[first+4]["close"]<=level


def stat(vals): return mod.stats([v for v in vals if v is not None])


def main():
    all_bars={}; by_code_daily={}; intr=[]; availability={}; errors={}
    for code,name in universe():
        try:
            bars=fetch_15m(code); all_bars[code]=bars; bd=defaultdict(list)
            for x in bars: bd[x["date"]].append(x)
            daily=derive_daily(code,name,bd); by_code_daily[code]=daily
            dm={r["date"]:r for r in daily}; ph=prior20_high(daily)
            rs=sigmod.intraday_features(code,name,bd,dm,ph)
            inject_breakout_60(rs,bd,ph)
            intr.extend(rs)
            availability[code]={"name":name,"bar_count":len(bars),"date_count":len(bd),"first":bars[0]["dt"].isoformat(),"last":bars[-1]["dt"].isoformat(),"derived_daily_count":len(daily)}
        except Exception as e: errors[code]=repr(e)

    panel=mod.enrich_future(by_code_daily); pmap={(r["date"],r["code"]):r for r in panel}
    by_date=defaultdict(list)
    for r in intr: by_date[r["date"]].append(r)
    for d,xs in by_date.items():
        med10=statistics.median([x["r1000_pct"] for x in xs if x.get("r1000_pct") is not None]) if any(x.get("r1000_pct") is not None for x in xs) else None
        medc=statistics.median([x["close_ret_pct"] for x in xs if x.get("close_ret_pct") is not None]) if any(x.get("close_ret_pct") is not None for x in xs) else None
        for x in xs:
            if med10 is None or medc is None or x.get("r1000_pct") is None or x.get("close_ret_pct") is None:
                x["signals"]["relative_strength_expand"]=False; x["signals"]["relative_strength_fade"]=False
            else:
                delta=(x["close_ret_pct"]-medc)-(x["r1000_pct"]-med10)
                x["signals"]["relative_strength_expand"]=delta>=1.0
                x["signals"]["relative_strength_fade"]=delta<=-1.0

    keys=["high_gap_fast_pullback","high_gap_hold_continue","low_gap_v_recovery","low_gap_continue_weak","high_gap_fill","high_gap_no_fill",
          "early_strong_pm_weak","early_strong_pm_reaccelerate","v_recovery_intraday","spike_reversal_no_reclaim","high_zone_consolidation",
          "tail_rally","tail_selloff","breakout_hold_60m","breakout_fail_60m","relative_strength_expand","relative_strength_fade"]
    result={}
    for key in keys:
        sel=[r for r in intr if r["signals"].get(key) and (r["date"],r["code"]) in pmap]
        z={"count":len(sel),"dates":len(set(r["date"] for r in sel)),"codes":len(set(r["code"] for r in sel)),"horizons":{}}
        for h in HORIZONS:
            qs=[pmap[(r["date"],r["code"])]["future"].get(h) for r in sel]
            qs=[q for q in qs if q]
            z["horizons"][str(h)]={"absolute":stat([q.get("return_pct") for q in qs]),"relative":stat([q.get("relative_to_pool_median_pct_points") for q in qs]),
                                       "mae":stat([q.get("mae_pct") for q in qs]),"mfe":stat([q.get("mfe_pct") for q in qs])}
        result[key]=z

    payload={"schema_version":"1.0","mode":"RESEARCH_ONLY_ASHARE_ANCESTRAL_SINA15_RETROSPECTIVE_PIT_SCREEN",
             "provider":"SINA_PUBLIC_15M_KLINE","provider_role":"research_only retrospective objective bars; signals use only information available by T",
             "availability":availability,"errors":errors,"intraday_rows":len(intr),"candidate_results":result,
             "decision_boundary":"No MASTER change, no production state, no automatic permission/action."}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"ashare_ancestral_intraday_sina15.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# A股祖训：新浪15分钟历史扩展验证（research-only）","",f"成功对象={len(availability)}，失败={len(errors)}，ETF×日={len(intr)}。",
           "","|候选|样本|日期|对象|T+1相对池|T+3相对池|T+5相对池|T+10相对池|T+5 MAE|T+5 MFE|","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k in keys:
        z=result[k]
        def m(h,field="relative"): return z["horizons"][str(h)][field].get("mean")
        lines.append(f"|{k}|{z['count']}|{z['dates']}|{z['codes']}|{m(1)}|{m(3)}|{m(5)}|{m(10)}|{m(5,'mae')}|{m(5,'mfe')}|")
    lines += ["","## 方法边界","","- 全部祖训阈值在结果前冻结，不做事后阈值搜索。","- 15分钟历史K由当前时点回溯取得，但每个信号只使用T日及此前信息；T+N只用于结果评价。","- 样本若集中于少数日期/对象，只记录为提示，不作稳定结论。","- 研究源不替代正式行情源。"]
    (OUT/"ashare_ancestral_intraday_sina15.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"ok":True,"availability":availability,"errors":errors,"rows":len(intr),"candidates":{k:{"n":v["count"],"t5":v["horizons"]["5"]["relative"].get("mean")} for k,v in result.items()}},ensure_ascii=False))

if __name__=="__main__": main()
