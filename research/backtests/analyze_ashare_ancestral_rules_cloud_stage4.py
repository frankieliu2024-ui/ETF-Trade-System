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

HORIZONS = (1, 3, 5, 10)
OLD_POSITIVE = ("healthy_breakout", "volume_recovery", "consolidation_breakout", "tech_risk_appetite")


def s(vals):
    return mod.stats([v for v in vals if v is not None])


def metric(rows, h, key):
    return s([r["future"][h].get(key) for r in rows if h in r.get("future", {}) and r["future"][h].get(key) is not None])


def summarize(rows):
    out = {"count": len(rows), "horizons": {}, "by_year": {}}
    for h in HORIZONS:
        out["horizons"][str(h)] = {
            "absolute": metric(rows, h, "return_pct"),
            "relative": metric(rows, h, "relative_to_pool_median_pct_points"),
            "mae": metric(rows, h, "mae_pct"),
            "mfe": metric(rows, h, "mfe_pct"),
        }
    years = defaultdict(list)
    for r in rows:
        years[r["date"][:4]].append(r)
    for y, xs in sorted(years.items()):
        out["by_year"][y] = {
            "count": len(xs),
            "t5_relative": metric(xs, 5, "relative_to_pool_median_pct_points"),
        }
    old = [r for r in rows if any(r.get("signals", {}).get(k) for k in OLD_POSITIVE)]
    out["old_6_2_overlap"] = {
        "count": len(old),
        "share": round(len(old) / len(rows), 4) if rows else 0.0,
    }
    clean = [r for r in rows if not any(r.get("signals", {}).get(k) for k in OLD_POSITIVE)]
    out["without_old_6_2"] = {
        "count": len(clean),
        "t5_relative": metric(clean, 5, "relative_to_pool_median_pct_points"),
        "t5_mae": metric(clean, 5, "mae_pct"),
        "t5_mfe": metric(clean, 5, "mfe_pct"),
    }
    return out


def nth_prev(rows, i, n=1):
    return rows[i-n] if i >= n else None


def three_down(rows, i):
    if i < 3:
        return False
    return all((rows[j].get("change_pct") or 0) < 0 for j in range(i-3, i))


def next_month_key(date_text):
    dt = datetime.fromisoformat(date_text)
    return (dt.year, dt.month)


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    by_key = {(r["date"], r["code"]): r for r in panel}
    idx_map = {}
    for code, rows in by_code.items():
        for i, r in enumerate(rows):
            idx_map[(r["date"], code)] = (rows, i)

    # Pre-register calendar-end buckets from available trading dates only.
    month_dates = defaultdict(list)
    quarter_dates = defaultdict(list)
    for d in dates:
        dt = datetime.fromisoformat(d)
        month_dates[(dt.year, dt.month)].append(d)
        quarter_dates[(dt.year, (dt.month - 1)//3 + 1)].append(d)
    month_last3 = {d for xs in month_dates.values() for d in xs[-3:]}
    quarter_last3 = {d for xs in quarter_dates.values() for d in xs[-3:]}

    groups = defaultdict(list)
    for r in panel:
        rows, i = idx_map[(r["date"], r["code"])]
        prev = nth_prev(rows, i, 1)
        if prev is None:
            continue
        open_ret = mod.pct(r.get("open"), prev.get("close"))
        clv = r.get("clv")
        day_ret = r.get("change_pct")
        prev_ret = prev.get("change_pct")

        # Opening structure, simple frozen 1% gap and 0.30/0.70 close-location buckets.
        if open_ret is not None and clv is not None:
            if open_ret >= 1.0 and clv <= 0.30:
                groups["gap_up_weak_close"].append(r)
            if open_ret >= 1.0 and clv >= 0.70:
                groups["gap_up_strong_close"].append(r)
            if open_ret <= -1.0 and clv >= 0.70:
                groups["gap_down_recovery_close"].append(r)
            if open_ret <= -1.0 and clv <= 0.30:
                groups["gap_down_weak_close"].append(r)

        # Prior extreme-day next-session paths. Thresholds are descriptive, not trading rules.
        if prev_ret is not None and open_ret is not None and clv is not None:
            if prev_ret >= 3.0:
                if open_ret >= 0.5 and clv <= 0.30:
                    groups["after_big_up_gap_up_weak_close"].append(r)
                elif open_ret >= 0.5 and clv >= 0.70:
                    groups["after_big_up_gap_up_strong_close"].append(r)
                elif open_ret <= -0.5 and clv >= 0.70:
                    groups["after_big_up_gap_down_repair"].append(r)
            if prev_ret <= -3.0:
                if open_ret >= 0.5 and clv >= 0.70:
                    groups["after_big_down_gap_up_repair"].append(r)
                elif open_ret <= -0.5 and clv >= 0.70:
                    groups["after_big_down_gap_down_repair"].append(r)
                elif open_ret <= -0.5 and clv <= 0.30:
                    groups["after_big_down_gap_down_weak"].append(r)

        # Three consecutive declines followed by first positive, high-quality close.
        if three_down(rows, i) and day_ret is not None and clv is not None:
            if day_ret > 0 and clv >= 0.70:
                groups["three_down_first_repair_high_close"].append(r)
            elif day_ret > 0 and clv <= 0.30:
                groups["three_down_first_repair_weak_close"].append(r)

        if r["date"] in month_last3:
            groups["month_end_last3_trading_days"].append(r)
        else:
            groups["non_month_end"].append(r)
        if r["date"] in quarter_last3:
            groups["quarter_end_last3_trading_days"].append(r)
        else:
            groups["non_quarter_end"].append(r)

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_ASHARE_ANCESTRAL_CLOUD_STAGE4",
        "date_range": [dates[0], dates[-1]],
        "panel_rows": len(panel),
        "definitions": {
            "gap": "open vs previous close; 1% for generic gap, 0.5% after prior +/-3% extreme day",
            "close_quality": "CLV <=0.30 weak, >=0.70 strong/recovery",
            "calendar_end": "last 3 observed A-share trading dates of each month/quarter",
            "three_down_repair": "three prior negative closes, current positive day; split by close location",
        },
        "groups": {k: summarize(v) for k, v in sorted(groups.items())},
        "decision_boundary": "Research-only descriptive PIT study. No MASTER change, trade signal, amount, sell share, or production integration.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ashare_ancestral_rules_cloud_stage4.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    labels = {
        "gap_up_weak_close": "高开后弱收盘",
        "gap_up_strong_close": "高开后强收盘",
        "gap_down_recovery_close": "低开后强修复",
        "gap_down_weak_close": "低开后弱收盘",
        "after_big_up_gap_up_weak_close": "大涨次日高开弱收",
        "after_big_up_gap_up_strong_close": "大涨次日高开强收",
        "after_big_up_gap_down_repair": "大涨次日低开修复",
        "after_big_down_gap_up_repair": "大跌次日高开修复",
        "after_big_down_gap_down_repair": "大跌次日低开修复",
        "after_big_down_gap_down_weak": "大跌次日低开续弱",
        "three_down_first_repair_high_close": "连跌后首次高质量修复",
        "three_down_first_repair_weak_close": "连跌后弱修复",
        "month_end_last3_trading_days": "月末最后3个交易日",
        "non_month_end": "非月末最后3日",
        "quarter_end_last3_trading_days": "季末最后3个交易日",
        "non_quarter_end": "非季末最后3日",
    }
    lines = [
        "# A股祖训云端日线 Stage4：开盘/极端日/连跌修复/月季末（自动生成）",
        "",
        f"覆盖：{dates[0]} 至 {dates[-1]}；ETF×交易日={len(panel)}。",
        "",
        "所有阈值为预注册粗分组，只用于判断是否值得继续；未来收益仅用于研究评价，不形成交易规则。",
        "",
        "|候选|样本|T+1相对池|T+5相对池|T+10相对池|T+5 MAE|T+5 MFE|旧6.2重叠|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in labels.items():
        z = payload["groups"].get(key, {"count":0,"horizons":{},"old_6_2_overlap":{"share":0}})
        def val(h, kind):
            x = (z.get("horizons", {}).get(str(h), {}).get(kind, {}) or {}).get("mean")
            return "NA" if x is None else f"{x:.3f}%"
        lines.append(f"|{label}|{z.get('count',0)}|{val(1,'relative')}|{val(5,'relative')}|{val(10,'relative')}|{val(5,'mae')}|{val(5,'mfe')}|{z.get('old_6_2_overlap',{}).get('share',0):.1%}|")
    lines += [
        "",
        "## 判读边界",
        "",
        "- 高开/低开分组只回答日K可见的开盘与收盘质量，不冒充盘中30/60分钟承接。",
        "- 大涨/大跌次日只作为条件化路径研究，不由单日涨跌直接生成动作。",
        "- 月末/季末若无稳定独立增量即停止，不为日历效应增加规则复杂度。",
        "- 只有低重叠、跨年度方向稳定且能够潜在改善Trial/Confirm、金额、卖出份额或资本比较的候选才进入下一层。",
    ]
    (OUT / "ashare_ancestral_rules_cloud_stage4.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "groups": {k: len(v) for k,v in groups.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
