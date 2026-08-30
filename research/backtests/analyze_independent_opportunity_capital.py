#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from revalidate_section6_baselines import load_panel, enrich_future, median, mean, stats

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research" / "reports" / "generated" / "independent_opportunity_capital"

# Current domestic-tech common-factor basket used only for this research comparison.
TECH_CODES = {"561980", "588000", "159781", "515880"}
# Existing observation directions with materially different primary return drivers.
INDEPENDENT_CODES = {"518880", "159992", "159326"}
HORIZONS = (5, 10)
MIGRATION_HURDLE_PCT_POINTS = 0.20


def ret_n(rows, idx, n):
    if idx < n:
        return None
    a = rows[idx - n].get("close")
    b = rows[idx].get("close")
    if a in (None, 0) or b is None:
        return None
    return (b / a - 1.0) * 100.0


def main():
    by_code, dates = load_panel()
    panel = enrich_future(by_code)
    by_date = defaultdict(list)
    row_index = {}
    for code, rows in by_code.items():
        for i, r in enumerate(rows):
            row_index[(code, r["date"])] = (rows, i)
    for r in panel:
        by_date[r["date"]].append(r)

    observations = []
    for d in sorted(by_date):
        rows = by_date[d]
        tech = [r for r in rows if r["code"] in TECH_CODES]
        indep = [r for r in rows if r["code"] in INDEPENDENT_CODES]
        if len(tech) < 3 or len(indep) < 2:
            continue

        tech_1d = [r.get("change_pct") for r in tech if r.get("change_pct") is not None]
        indep_1d = [r.get("change_pct") for r in indep if r.get("change_pct") is not None]
        tech_5d, indep_5d = [], []
        for r in tech:
            rows_i, idx = row_index[(r["code"], d)]
            x = ret_n(rows_i, idx, 5)
            if x is not None:
                tech_5d.append(x)
        for r in indep:
            rows_i, idx = row_index[(r["code"], d)]
            x = ret_n(rows_i, idx, 5)
            if x is not None:
                indep_5d.append(x)
        if len(tech_1d) < 3 or len(indep_1d) < 2 or len(tech_5d) < 3 or len(indep_5d) < 2:
            continue

        current_divergence = median(indep_1d) > median(tech_1d) and median(indep_5d) > median(tech_5d)
        record = {
            "date": d,
            "dual_divergence": bool(current_divergence),
            "tech_1d_median": median(tech_1d),
            "independent_1d_median": median(indep_1d),
            "tech_5d_median": median(tech_5d),
            "independent_5d_median": median(indep_5d),
            "future": {},
        }
        for h in HORIZONS:
            tech_f = [r["future"][h]["return_pct"] for r in tech if h in r["future"] and r["future"][h].get("return_pct") is not None]
            indep_f = [r["future"][h]["return_pct"] for r in indep if h in r["future"] and r["future"][h].get("return_pct") is not None]
            if len(tech_f) < 3 or len(indep_f) < 2:
                continue
            tech_med = median(tech_f)
            indep_med = median(indep_f)
            spread = indep_med - tech_med
            record["future"][h] = {
                "tech_median_return_pct": tech_med,
                "independent_median_return_pct": indep_med,
                "independent_minus_tech_pct_points": spread,
                "after_20bp_migration_hurdle_pct_points": spread - MIGRATION_HURDLE_PCT_POINTS,
                "independent_wins": spread > 0,
                "clears_migration_hurdle": spread > MIGRATION_HURDLE_PCT_POINTS,
            }
        observations.append(record)

    def summarize(selected, h):
        vals = [r["future"][h]["independent_minus_tech_pct_points"] for r in selected if h in r["future"]]
        net = [r["future"][h]["after_20bp_migration_hurdle_pct_points"] for r in selected if h in r["future"]]
        wins = [r["future"][h]["independent_wins"] for r in selected if h in r["future"]]
        clears = [r["future"][h]["clears_migration_hurdle"] for r in selected if h in r["future"]]
        return {
            "spread": stats(vals),
            "after_20bp_hurdle": stats(net),
            "independent_win_rate": round(mean([1.0 if x else 0.0 for x in wins]), 4) if wins else None,
            "clear_20bp_hurdle_rate": round(mean([1.0 if x else 0.0 for x in clears]), 4) if clears else None,
        }

    signal = [r for r in observations if r["dual_divergence"]]
    nonsignal = [r for r in observations if not r["dual_divergence"]]
    summary = {
        "all_dates": {str(h): summarize(observations, h) for h in HORIZONS},
        "dual_divergence": {str(h): summarize(signal, h) for h in HORIZONS},
        "non_divergence": {str(h): summarize(nonsignal, h) for h in HORIZONS},
    }

    # Pre-registered interpretation: evidence is promising only if both horizons
    # show positive mean spread after the 20bp migration hurdle and >50% win rate.
    promising = True
    for h in HORIZONS:
        s = summary["dual_divergence"][str(h)]
        if s["after_20bp_hurdle"].get("mean") is None or s["after_20bp_hurdle"]["mean"] <= 0:
            promising = False
        if s.get("independent_win_rate") is None or s["independent_win_rate"] <= 0.5:
            promising = False

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_INDEPENDENT_OPPORTUNITY_CAPITAL",
        "date_range": [dates[0] if dates else None, dates[-1] if dates else None],
        "tech_codes": sorted(TECH_CODES),
        "independent_codes": sorted(INDEPENDENT_CODES),
        "definition": "At T close, independent group median 1d return > domestic-tech group median 1d return AND independent group median trailing-5d return > domestic-tech group median trailing-5d return.",
        "migration_hurdle_pct_points": MIGRATION_HURDLE_PCT_POINTS,
        "eligible_dates": len(observations),
        "dual_divergence_dates": len(signal),
        "summary": summary,
        "research_interpretation": "PROMISING_INDEPENDENT_CAPITAL_COMPARISON_EVIDENCE" if promising else "NO_STABLE_INCREMENT",
        "decision_eligible": False,
        "trade_signal": None,
        "production_integration": False,
        "boundary": "Research-only capital-comparison evidence. It does not create diversification targets, mechanical rotation, risk permission, Trial/Confirm, amount, sell quantity or orders.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "independent_opportunity_capital.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 低相关方向与科技暴露：下一单位资本条件比较（research-only）",
        "",
        f"- 覆盖：{payload['date_range'][0]} 至 {payload['date_range'][1]}",
        f"- 合格比较日：{len(observations)}；双重背离日：{len(signal)}",
        "- 双重背离定义：T日收盘时，低相关组的当日收益中位数与过去5日收益中位数均高于国内科技组。",
        "- 未来收益仅用于研究评价；不重建历史ChatGPT决策。",
        "- 20bp仅作为资本迁移的保守收益门槛，不是MASTER交易成本规则。",
        "",
        "|条件|周期|未来低相关-科技中位收益差均值|扣20bp后均值|低相关胜率|跨过20bp比例|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in [("dual_divergence", "双重背离"), ("non_divergence", "非背离"), ("all_dates", "全部")]:
        for h in HORIZONS:
            s = summary[key][str(h)]
            spread = s["spread"].get("mean")
            net = s["after_20bp_hurdle"].get("mean")
            wr = s.get("independent_win_rate")
            cr = s.get("clear_20bp_hurdle_rate")
            lines.append(f"|{label}|T+{h}|{spread if spread is not None else 'NA'}|{net if net is not None else 'NA'}|{wr if wr is not None else 'NA'}|{cr if cr is not None else 'NA'}|")
    lines += [
        "",
        f"结论：**{payload['research_interpretation']}**",
        "",
        "解释边界：该研究只检验已有观察方向在科技暴露效率下降时是否具有样本外资本比较价值；不建立固定配置比例，不机械卖科技买低相关，不新增评分或平行交易入口。",
    ]
    (OUT / "independent_opportunity_capital.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "eligible_dates": len(observations), "dual_divergence_dates": len(signal), "interpretation": payload["research_interpretation"], "summary": summary["dual_divergence"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
