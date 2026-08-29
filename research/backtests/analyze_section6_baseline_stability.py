#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "research" / "backtests" / "revalidate_section6_baselines.py"
OUT_DIR = ROOT / "research" / "reports" / "generated" / "section6_revalidation"

spec = importlib.util.spec_from_file_location("section6_base", BASELINE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

SIGNALS = [
    "healthy_breakout",
    "volume_recovery",
    "consolidation_breakout",
    "tech_risk_appetite",
    "consecutive_rise_risk_review",
]
LABELS = {
    "healthy_breakout": "健康突破",
    "volume_recovery": "放量修复",
    "consolidation_breakout": "整理突破",
    "tech_risk_appetite": "科技风险偏好改善",
    "consecutive_rise_risk_review": "连续上涨风险复核",
}


def metric(rows, h=5, key="return_pct"):
    vals = [r["future"][h].get(key) for r in rows if h in r["future"] and r["future"][h].get(key) is not None]
    return mod.stats(vals)


def rel_metric(rows, h=5):
    vals = [r["future"][h].get("relative_to_pool_median_pct_points") for r in rows if h in r["future"] and r["future"][h].get("relative_to_pool_median_pct_points") is not None]
    return mod.stats(vals)


def group_summary(rows, field, h=5):
    out = {}
    groups = defaultdict(list)
    for r in rows:
        groups[str(r[field])].append(r)
    for k, xs in sorted(groups.items()):
        out[k] = {
            "count": len(xs),
            "absolute": metric(xs, h),
            "relative": rel_metric(xs, h),
            "mae": metric(xs, h, "mae_pct"),
            "mfe": metric(xs, h, "mfe_pct"),
        }
    return out


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    for r in panel:
        r["year"] = r["date"][:4]

    eligible5 = [r for r in panel if 5 in r["future"]]
    base = {
        "count": len(eligible5),
        "absolute": metric(eligible5, 5),
        "relative": rel_metric(eligible5, 5),
        "mae": metric(eligible5, 5, "mae_pct"),
        "mfe": metric(eligible5, 5, "mfe_pct"),
    }

    signals = {}
    for key in SIGNALS:
        selected = [r for r in panel if r["signals"].get(key) and 5 in r["future"]]
        abs5 = metric(selected, 5)
        rel5 = rel_metric(selected, 5)
        signals[key] = {
            "count": len(selected),
            "absolute": abs5,
            "relative": rel5,
            "mae": metric(selected, 5, "mae_pct"),
            "mfe": metric(selected, 5, "mfe_pct"),
            "increment_vs_all_rows_mean_pct_points": None if abs5.get("mean") is None or base["absolute"].get("mean") is None else round(abs5["mean"] - base["absolute"]["mean"], 4),
            "by_year": group_summary(selected, "year", 5),
            "by_code": group_summary(selected, "code", 5),
        }

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_SECTION6_STABILITY_ANALYSIS",
        "date_range": [dates[0], dates[-1]],
        "baseline_all_etf_days_t5": base,
        "signals": signals,
        "decision_boundary": "Descriptive stability/incremental analysis only. No parameter selection, no formal rule change.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "section6_baseline_stability.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# MASTER 6.2 五项依据：T+5稳定性与增量初筛（自动生成）",
        "",
        f"完整ETF×交易日T+5可评估样本：{base['count']}；全样本T+5均值={base['absolute'].get('mean')}%，相对ETF池均值={base['relative'].get('mean')}%。",
        "",
        "|依据|样本|T+5均值|相对池均值|相对全样本增量|T+5 MAE|T+5 MFE|年度方向一致性|",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key in SIGNALS:
        z = signals[key]
        years = z["by_year"]
        signs = []
        for y, yz in years.items():
            m = yz["relative"].get("mean")
            signs.append(f"{y}:{'+' if (m or 0)>0 else '-' if (m or 0)<0 else '0'}({yz['count']})")
        lines.append(
            f"|{LABELS[key]}|{z['count']}|{z['absolute'].get('mean')}%|{z['relative'].get('mean')}%|{z['increment_vs_all_rows_mean_pct_points']}pp|{z['mae'].get('mean')}%|{z['mfe'].get('mean')}%|{' / '.join(signs)}|"
        )
    lines += [
        "",
        "## 判读约束",
        "",
        "- 这里的“相对全样本增量”只是描述性差值，不是因果增量；趋势状态、对象构成和市场年份可能共同解释差异。",
        "- 年度方向不一致、单对象高度集中或样本过少时，不得直接保留/删除；下一步需做组件消融、市场状态分层和相关证据条件化。",
        "- 连续上涨风险复核应主要评价风险缓释与赢家右尾损失的权衡；负的未来相对收益只是支持其继续研究，不等于机械卖出授权。",
        "- 本轮不调任何MASTER阈值。",
        "",
    ]
    (OUT_DIR / "section6_baseline_stability.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": True, "base_t5_mean": base["absolute"].get("mean"), "signals": {k: {"n": v["count"], "t5": v["absolute"].get("mean"), "rel5": v["relative"].get("mean"), "increment": v["increment_vs_all_rows_mean_pct_points"]} for k,v in signals.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
