#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research/backtests/revalidate_section6_baselines.py"
OUT = ROOT / "research/reports/generated/section6_revalidation"

spec = importlib.util.spec_from_file_location("section6_base", BASE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

H = (1, 3, 5, 10)
OLD = ("healthy_breakout", "volume_recovery", "consolidation_breakout", "tech_risk_appetite")


def stat(rows, h, key):
    vals = [r["future"][h].get(key) for r in rows if h in r.get("future", {}) and r["future"][h].get(key) is not None]
    return mod.stats(vals)


def summary(rows):
    old = [r for r in rows if any(r.get("signals", {}).get(k) for k in OLD)]
    clean = [r for r in rows if not any(r.get("signals", {}).get(k) for k in OLD)]
    years = defaultdict(list)
    for r in rows:
        years[r["date"][:4]].append(r)
    return {
        "count": len(rows),
        "horizons": {str(h): {
            "absolute": stat(rows,h,"return_pct"),
            "relative": stat(rows,h,"relative_to_pool_median_pct_points"),
            "mae": stat(rows,h,"mae_pct"),
            "mfe": stat(rows,h,"mfe_pct"),
        } for h in H},
        "by_year": {y: {"count": len(xs), "t5_relative": stat(xs,5,"relative_to_pool_median_pct_points")} for y,xs in sorted(years.items())},
        "old_6_2_overlap": {"count": len(old), "share": round(len(old)/len(rows),4) if rows else 0.0},
        "without_old_6_2": {
            "count": len(clean),
            "t5_relative": stat(clean,5,"relative_to_pool_median_pct_points"),
            "t5_mae": stat(clean,5,"mae_pct"),
            "t5_mfe": stat(clean,5,"mfe_pct"),
        },
    }


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    row_map = {(r["date"], r["code"]): r for r in panel}
    idx = {}
    for code, rows in by_code.items():
        for i, r in enumerate(rows):
            idx[(r["date"], code)] = (rows, i)

    month_dates, quarter_dates = defaultdict(list), defaultdict(list)
    for d in dates:
        dt = datetime.fromisoformat(d)
        month_dates[(dt.year, dt.month)].append(d)
        quarter_dates[(dt.year, (dt.month-1)//3+1)].append(d)
    month_last3 = {d for xs in month_dates.values() for d in xs[-3:]}
    quarter_last3 = {d for xs in quarter_dates.values() for d in xs[-3:]}

    g = defaultdict(list)
    for r in panel:
        rows, i = idx[(r["date"], r["code"])]
        if i < 1:
            continue
        prev = rows[i-1]
        open_ret = mod.pct(r.get("open"), prev.get("close"))
        clv = r.get("clv")
        day_ret = r.get("change_pct")
        prev_ret = prev.get("change_pct")

        if open_ret is not None and clv is not None:
            if open_ret >= 1.0 and clv <= 0.30: g["gap_up_weak_close"].append(r)
            if open_ret >= 1.0 and clv >= 0.70: g["gap_up_strong_close"].append(r)
            if open_ret <= -1.0 and clv >= 0.70: g["gap_down_recovery_close"].append(r)
            if open_ret <= -1.0 and clv <= 0.30: g["gap_down_weak_close"].append(r)

        if prev_ret is not None and open_ret is not None and clv is not None:
            if prev_ret >= 3.0:
                if open_ret >= 0.5 and clv <= 0.30: g["after_big_up_gap_up_weak_close"].append(r)
                elif open_ret >= 0.5 and clv >= 0.70: g["after_big_up_gap_up_strong_close"].append(r)
                elif open_ret <= -0.5 and clv >= 0.70: g["after_big_up_gap_down_repair"].append(r)
            if prev_ret <= -3.0:
                if open_ret >= 0.5 and clv >= 0.70: g["after_big_down_gap_up_repair"].append(r)
                elif open_ret <= -0.5 and clv >= 0.70: g["after_big_down_gap_down_repair"].append(r)
                elif open_ret <= -0.5 and clv <= 0.30: g["after_big_down_gap_down_weak"].append(r)

        if i >= 3 and all((rows[j].get("change_pct") or 0) < 0 for j in range(i-3, i)) and day_ret is not None and clv is not None and day_ret > 0:
            if clv >= 0.70: g["three_down_first_repair_high_close"].append(r)
            elif clv <= 0.30: g["three_down_first_repair_weak_close"].append(r)

        if r["date"] in month_last3: g["month_end_last3"].append(r)
        else: g["non_month_end"].append(r)
        if r["date"] in quarter_last3: g["quarter_end_last3"].append(r)
        else: g["non_quarter_end"].append(r)

    payload = {
        "schema_version":"1.0",
        "mode":"RESEARCH_ONLY_ASHARE_ANCESTRAL_CLOUD_STAGE4",
        "issue":107,
        "date_range":[dates[0], dates[-1]],
        "panel_rows":len(panel),
        "definitions":{
            "generic_gap":"open vs previous close >= +1% or <= -1%",
            "extreme_prior_day":"previous close return >= +3% or <= -3%; next-day gap bucket uses 0.5%",
            "close_quality":"CLV <=0.30 weak, >=0.70 strong/recovery",
            "three_down_repair":"three prior negative days followed by positive day",
            "calendar_end":"last 3 observed trading dates of month/quarter",
        },
        "groups":{k:summary(v) for k,v in sorted(g.items())},
        "decision_boundary":"Research-only PIT study; no trade signal, amount, sell share, MASTER change or production integration.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/"ashare_ancestral_rules_cloud_stage4.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    labels={
        "gap_up_weak_close":"高开后弱收盘","gap_up_strong_close":"高开后强收盘","gap_down_recovery_close":"低开后强修复","gap_down_weak_close":"低开后弱收盘",
        "after_big_up_gap_up_weak_close":"大涨次日高开弱收","after_big_up_gap_up_strong_close":"大涨次日高开强收","after_big_up_gap_down_repair":"大涨次日低开修复",
        "after_big_down_gap_up_repair":"大跌次日高开修复","after_big_down_gap_down_repair":"大跌次日低开修复","after_big_down_gap_down_weak":"大跌次日低开续弱",
        "three_down_first_repair_high_close":"连跌后首次高质量修复","three_down_first_repair_weak_close":"连跌后弱修复","month_end_last3":"月末最后3日","non_month_end":"非月末最后3日","quarter_end_last3":"季末最后3日","non_quarter_end":"非季末最后3日"}
    lines=["# A股祖训云端日线 Stage4：开盘/极端日/连跌修复/月季末","",f"覆盖：{dates[0]} 至 {dates[-1]}；ETF×交易日={len(panel)}。","","|候选|样本|T+1相对池|T+5相对池|T+10相对池|T+5 MAE|T+5 MFE|旧6.2重叠|","|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key,label in labels.items():
        z=payload["groups"].get(key,{"count":0,"horizons":{},"old_6_2_overlap":{"share":0}})
        def v(h,k):
            x=(z.get("horizons",{}).get(str(h),{}).get(k,{}) or {}).get("mean")
            return "NA" if x is None else f"{x:.3f}%"
        lines.append(f"|{label}|{z.get('count',0)}|{v(1,'relative')}|{v(5,'relative')}|{v(10,'relative')}|{v(5,'mae')}|{v(5,'mfe')}|{z.get('old_6_2_overlap',{}).get('share',0):.1%}|")
    lines += ["","判读：只保留低重叠、跨年度方向稳定且能潜在改善Trial/Confirm、金额、卖出份额或资本比较的候选；日K分组不冒充盘中承接。"]
    text="\n".join(lines)+"\n"
    (OUT/"ashare_ancestral_rules_cloud_stage4.md").write_text(text,encoding="utf-8")
    # Existing one-off workflow still cats the stage1 filename; mirror the Stage4 report there for this isolated run only.
    (OUT/"ashare_ancestral_rules_cloud_stage1.md").write_text(text,encoding="utf-8")
    print(json.dumps({"ok":True,"stage":4,"groups":{k:len(v) for k,v in g.items()}},ensure_ascii=False))

if __name__ == "__main__":
    main()
