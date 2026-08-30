#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research/backtests/revalidate_section6_baselines.py"
OUT = ROOT / "research/reports/generated/section6_revalidation"

spec = importlib.util.spec_from_file_location("section6_base", BASE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

H=(1,3,5,10)
OLD=("healthy_breakout","volume_recovery","consolidation_breakout","tech_risk_appetite")


def st(rows,h,key):
    return mod.stats([r["future"][h].get(key) for r in rows if h in r.get("future",{}) and r["future"][h].get(key) is not None])


def sm(rows):
    return {"count":len(rows),"h":{str(h):{"rel":st(rows,h,"relative_to_pool_median_pct_points"),"abs":st(rows,h,"return_pct"),"mae":st(rows,h,"mae_pct"),"mfe":st(rows,h,"mfe_pct")} for h in H}}


def main():
    by_code,dates=mod.load_panel()
    panel=mod.enrich_future(by_code)
    row_map={(r["date"],r["code"]):r for r in panel}
    idx={}
    by_date=defaultdict(list)
    for r in panel: by_date[r["date"]].append(r)
    for code,rows in by_code.items():
        for i,r in enumerate(rows): idx[(r["date"],code)]=(rows,i)

    date_ctx={}
    for d,rows in by_date.items():
        vals=[r.get("change_pct") for r in rows if r.get("change_pct") is not None]
        med=mod.median(vals)
        pos=sum(v>0 for v in vals)/len(vals) if vals else None
        date_ctx[d]={"median":med,"positive_share":pos}

    groups=defaultdict(list)
    years=defaultdict(lambda: defaultdict(list))
    objects=defaultdict(lambda: defaultdict(list))
    for r in panel:
        rows,i=idx[(r["date"],r["code"])]
        if i<1: continue
        prev=rows[i-1]
        gap=mod.pct(r.get("open"),prev.get("close"))
        clv=r.get("clv")
        if gap is None or gap>-1.0 or clv is None: continue
        label="strong_recovery" if clv>=0.70 else ("weak_close" if clv<=0.30 else "middle_close")
        groups[label].append(r)
        years[label][r["date"][:4]].append(r)
        objects[label][r["code"]].append(r)

        ma20=mod.sma_close(rows,i,20,include_current=True)
        ret20=mod.ret_n(rows,i,20)
        trend="uptrend" if ret20 is not None and ret20>0 and ma20 is not None and r.get("close") is not None and r["close"]>=ma20 else "non_uptrend"
        groups[f"{label}__{trend}"].append(r)

        ctx=date_ctx.get(r["date"],{})
        breadth=ctx.get("positive_share")
        if breadth is not None:
            regime="weak_market" if breadth<=0.35 else ("strong_market" if breadth>=0.65 else "middle_market")
            groups[f"{label}__{regime}"].append(r)
        med=ctx.get("median")
        if med is not None and r.get("change_pct") is not None:
            rel=r["change_pct"]-med
            rel_state="relative_leader" if rel>=1.0 else ("relative_laggard" if rel<=-1.0 else "relative_middle")
            groups[f"{label}__{rel_state}"].append(r)

        oldhit=any(r.get("signals",{}).get(k) for k in OLD)
        if not oldhit: groups[f"{label}__without_old6_2"].append(r)

    payload={
        "schema_version":"1.0","mode":"RESEARCH_ONLY_ASHARE_ANCESTRAL_STAGE5_LOW_GAP_RECOVERY",
        "issue":107,"date_range":[dates[0],dates[-1]],"panel_rows":len(panel),
        "definition":"gap <= -1% vs prior close; strong recovery CLV>=0.70, weak close CLV<=0.30; trend/breadth/relative buckets frozen before outcome review",
        "groups":{k:sm(v) for k,v in sorted(groups.items())},
        "by_year":{k:{y:sm(v) for y,v in sorted(g.items())} for k,g in years.items()},
        "by_code":{k:{c:sm(v) for c,v in sorted(g.items())} for k,g in objects.items()},
        "decision_boundary":"Research-only conditional/dedup validation; no trade rule, amount, sell share, ranking or production integration."
    }
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"ashare_ancestral_rules_cloud_stage5.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    keys=["strong_recovery","weak_close","middle_close","strong_recovery__without_old6_2","strong_recovery__uptrend","strong_recovery__non_uptrend","strong_recovery__weak_market","strong_recovery__strong_market","strong_recovery__relative_leader","strong_recovery__relative_laggard"]
    lines=["# A股祖训Stage5：低开后强修复条件化验证","",f"覆盖：{dates[0]} 至 {dates[-1]}；ETF×交易日={len(panel)}。","","|分组|样本|T+1相对池|T+5相对池|T+10相对池|T+5 MAE|T+5 MFE|","|---|---:|---:|---:|---:|---:|---:|"]
    for k in keys:
        z=payload["groups"].get(k,{"count":0,"h":{}})
        def v(h,key):
            x=(z.get("h",{}).get(str(h),{}).get(key,{}) or {}).get("mean")
            return "NA" if x is None else f"{x:.3f}%"
        lines.append(f"|{k}|{z.get('count',0)}|{v(1,'rel')}|{v(5,'rel')}|{v(10,'rel')}|{v(5,'mae')}|{v(5,'mfe')}|")
    lines += ["","年度强修复："]
    for y,z in payload["by_year"].get("strong_recovery",{}).items():
        x=(z["h"]["5"]["rel"] or {}).get("mean")
        lines.append(f"- {y}: n={z['count']}, T+5相对ETF池={x}%")
    lines += ["","判读：只有强修复相对同样低开背景的弱/中性收盘保持稳定增量，并在趋势/市场宽度/相对强弱分层中不过度依赖单一状态，才值得未来做正式转化审查；否则留作普通价格反馈。"]
    text="\n".join(lines)+"\n"
    (OUT/"ashare_ancestral_rules_cloud_stage5.md").write_text(text,encoding="utf-8")
    (OUT/"ashare_ancestral_rules_cloud_stage1.md").write_text(text,encoding="utf-8")
    print(json.dumps({"ok":True,"stage":5,"strong":len(groups['strong_recovery']),"weak":len(groups['weak_close'])},ensure_ascii=False))

if __name__=="__main__": main()
