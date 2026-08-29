#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict

from analyze_section6_component_ablation import add_date_regime, attach_components, signal_mask
from revalidate_section6_baselines import OUT_DIR, TECH_CODES, load_panel, mean, ret_n

SIGNALS = [
    "healthy_breakout",
    "volume_recovery",
    "consolidation_breakout",
    "tech_risk_appetite",
    "consecutive_rise_risk_review",
]


def trend_bin(v):
    if v is None:
        return "UNKNOWN"
    if v >= 5.0:
        return "UP_20D"
    if v <= -5.0:
        return "DOWN_20D"
    return "FLAT_20D"


def add_ret20(by_code, rows):
    index = {(r["code"], r["date"]): r for r in rows}
    for code, series in by_code.items():
        for i, raw in enumerate(series):
            row = index[(code, raw["date"])]
            row["ret20"] = ret_n(series, i, 20)
            row["trend_bin"] = trend_bin(row["ret20"])
    return rows


def eligible_control(row, signal):
    # Keep controls in the same natural object scope without adding new signal rules.
    if signal == "tech_risk_appetite":
        return row.get("code") in TECH_CODES
    return True


def conditional_effect(rows, signal):
    usable = [r for r in rows if 5 in r.get("future", {}) and eligible_control(r, signal)]
    strata = defaultdict(lambda: {"signal": [], "control": []})
    for r in usable:
        key = (r["code"], r["date"][:4], r.get("market_regime"), r.get("trend_bin"))
        target = "signal" if signal_mask(r, signal) else "control"
        strata[key][target].append(r)

    matched_signal_n = 0
    weighted_abs_diffs = []
    weighted_rel_diffs = []
    stratum_rows = []
    for key, g in strata.items():
        if not g["signal"] or not g["control"]:
            continue
        s_abs = mean([r["future"][5].get("return_pct") for r in g["signal"]])
        c_abs = mean([r["future"][5].get("return_pct") for r in g["control"]])
        s_rel = mean([r["future"][5].get("relative_to_pool_median_pct_points") for r in g["signal"]])
        c_rel = mean([r["future"][5].get("relative_to_pool_median_pct_points") for r in g["control"]])
        n = len(g["signal"])
        matched_signal_n += n
        if s_abs is not None and c_abs is not None:
            weighted_abs_diffs.extend([s_abs - c_abs] * n)
        if s_rel is not None and c_rel is not None:
            weighted_rel_diffs.extend([s_rel - c_rel] * n)
        stratum_rows.append({
            "code": key[0], "year": key[1], "market_regime": key[2], "trend_bin": key[3],
            "signal_n": n, "control_n": len(g["control"]),
            "signal_abs5": s_abs, "control_abs5": c_abs,
            "signal_rel5": s_rel, "control_rel5": c_rel,
        })

    total_signal_n = sum(1 for r in usable if signal_mask(r, signal))
    return {
        "total_signal_n": total_signal_n,
        "matched_signal_n": matched_signal_n,
        "matched_coverage": 0 if total_signal_n == 0 else round(matched_signal_n / total_signal_n, 4),
        "conditional_abs5_diff_pp": None if not weighted_abs_diffs else round(mean(weighted_abs_diffs), 4),
        "conditional_rel5_diff_pp": None if not weighted_rel_diffs else round(mean(weighted_rel_diffs), 4),
        "matched_strata": len(stratum_rows),
        "strata": stratum_rows,
    }


def fmt(x):
    return "NA" if x is None else f"{x:+.3f}pp"


def render(payload):
    labels = {
        "healthy_breakout": "健康突破",
        "volume_recovery": "放量修复",
        "consolidation_breakout": "整理突破",
        "tech_risk_appetite": "科技风险偏好改善",
        "consecutive_rise_risk_review": "连续上涨风险复核",
    }
    lines = [
        "# MASTER 6.2 条件化增量初筛（自动生成）",
        "",
        "为减少年份、对象和基础趋势混杂，本轮在同一ETF、同一年、同一T日市场横截面状态、同一20日趋势粗分箱内，用未命中日作对照，比较T+5结果。",
        "",
        "这不是因果估计，也不是参数优化；仅用于判断首轮描述性增量在基础条件控制后是否仍存在。",
        "",
        "|依据|信号样本|可匹配样本|覆盖|条件化T+5差值|条件化相对ETF池差值|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for k, label in labels.items():
        x = payload["signals"][k]
        lines.append(f"|{label}|{x['total_signal_n']}|{x['matched_signal_n']}|{x['matched_coverage']:.1%}|{fmt(x['conditional_abs5_diff_pp'])}|{fmt(x['conditional_rel5_diff_pp'])}|")
    lines += [
        "",
        "## 边界",
        "",
        "- 控制变量只使用当期可观察的对象、年份、横截面市场状态与20日趋势粗分箱；不使用未来信息挑选匹配。",
        "- 同一分层内仍可能存在成交、波动、对象特征等未控制混杂，因此结果不能解释为因果alpha。",
        "- 若条件化增量显著收缩，说明旧证据可能主要重复基础趋势/市场环境；若仍保留，则进入后续云端证据条件化与稳健性研究。",
        "- 连续上涨风险复核的负条件化差值只支持风险复核价值，不产生机械卖出权限。",
        "",
    ]
    return "\n".join(lines)


def main():
    by_code, dates = load_panel()
    rows = add_ret20(by_code, add_date_regime(attach_components(by_code)))
    signals = {s: conditional_effect(rows, s) for s in SIGNALS}
    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_SECTION6_CONDITIONAL_INCREMENT",
        "point_in_time": True,
        "parameter_optimization": False,
        "date_range": [dates[0], dates[-1]] if dates else [],
        "matching": "same code + year + same-day ETF cross-sectional regime + 20d trend coarse bin",
        "signals": signals,
        "decision_boundary": "Research-only conditional screen; no trading authority.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "section6_conditional_increment.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT_DIR / "section6_conditional_increment.md").write_text(render(payload) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "signals": {k: {q: v[q] for q in ["matched_coverage", "conditional_abs5_diff_pp", "conditional_rel5_diff_pp"]} for k, v in signals.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
