#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from revalidate_section6_baselines import (
    OUT_DIR,
    TECH_CODES,
    amount_ratio,
    breakout_high,
    enrich_future,
    five_up,
    load_panel,
    mean,
    pct,
    prior_range_amp,
    ret_n,
    sma_close,
)


def build_components(rows, idx):
    r = rows[idx]
    clv = r.get("clv")
    ret5 = ret_n(rows, idx, 5)
    amt20 = amount_ratio(rows, idx, 20, include_current=False)
    ma20 = sma_close(rows, idx, 20, include_current=True)
    prev_ma20 = sma_close(rows, idx - 1, 20, include_current=True) if idx > 0 else None
    prev_close = rows[idx - 1]["close"] if idx > 0 else None
    amp10 = prior_range_amp(rows, idx, 10)
    daily = r.get("change_pct")
    return {
        "healthy_breakout": {
            "breakout60": breakout_high(rows, idx, 60),
            "amount_ge_1x": amt20 is not None and amt20 >= 1.0,
            "clv_ge_070": clv is not None and clv >= 0.70,
            "ret5_le_10": ret5 is not None and ret5 <= 10.0,
        },
        "volume_recovery": {
            "cross_ma20": (
                prev_close is not None and prev_ma20 is not None and prev_close < prev_ma20
                and r.get("close") is not None and ma20 is not None and r["close"] >= ma20
            ),
            "amount_ge_1x": amt20 is not None and amt20 >= 1.0,
            "clv_ge_070": clv is not None and clv >= 0.70,
        },
        "consolidation_breakout": {
            "prior10_amp_le_6": amp10 is not None and amp10 <= 6.0,
            "breakout20": breakout_high(rows, idx, 20),
            "amount_ge_1p2x": amt20 is not None and amt20 >= 1.2,
            "clv_ge_075": clv is not None and clv >= 0.75,
        },
        "tech_risk_appetite": {
            "tech_scope": r.get("code") in TECH_CODES,
            "daily_ge_3": daily is not None and daily >= 3.0,
            "clv_ge_075": clv is not None and clv >= 0.75,
            "amount_1_to_1p8x": amt20 is not None and 1.0 <= amt20 <= 1.8,
            "ret5_le_8": ret5 is not None and ret5 <= 8.0,
        },
        "consecutive_rise_risk_review": {
            "five_up": five_up(rows, idx),
            "ret5_ge_10": ret5 is not None and ret5 >= 10.0,
        },
    }


def attach_components(by_code):
    rows_out = []
    enriched = enrich_future(by_code)
    lookup = {(r["code"], r["date"]): r for r in enriched}
    for code, rows in by_code.items():
        for idx, r in enumerate(rows):
            x = lookup[(code, r["date"])]
            x["components"] = build_components(rows, idx)
            rows_out.append(x)
    return rows_out


def metric(selected, horizon=5):
    eligible = [r for r in selected if horizon in r.get("future", {})]
    abs_ret = [r["future"][horizon].get("return_pct") for r in eligible]
    rel_ret = [r["future"][horizon].get("relative_to_pool_median_pct_points") for r in eligible]
    mae = [r["future"][horizon].get("mae_pct") for r in eligible]
    mfe = [r["future"][horizon].get("mfe_pct") for r in eligible]
    return {
        "n": len(eligible),
        "t5_mean": None if not eligible else round(mean(abs_ret), 4),
        "rel5_mean": None if not eligible else round(mean(rel_ret), 4),
        "mae5_mean": None if not eligible else round(mean(mae), 4),
        "mfe5_mean": None if not eligible else round(mean(mfe), 4),
    }


def signal_mask(row, signal, omitted=None):
    parts = row["components"][signal]
    return all(v for k, v in parts.items() if k != omitted)


def evaluate_signal(rows, signal):
    baseline = [r for r in rows if signal_mask(r, signal)]
    base_metric = metric(baseline)
    component_names = list(next(r["components"][signal] for r in rows if signal in r["components"]).keys())
    ablations = {}
    for component in component_names:
        selected = [r for r in rows if signal_mask(r, signal, omitted=component)]
        m = metric(selected)
        m["delta_rel5_vs_baseline"] = None if m["rel5_mean"] is None or base_metric["rel5_mean"] is None else round(m["rel5_mean"] - base_metric["rel5_mean"], 4)
        m["delta_t5_vs_baseline"] = None if m["t5_mean"] is None or base_metric["t5_mean"] is None else round(m["t5_mean"] - base_metric["t5_mean"], 4)
        ablations[component] = m

    by_year = {}
    for year in sorted({r["date"][:4] for r in baseline}):
        by_year[year] = metric([r for r in baseline if r["date"].startswith(year)])
    by_code = {}
    for code in sorted({r["code"] for r in baseline}):
        by_code[code] = metric([r for r in baseline if r["code"] == code])
    return {"baseline": base_metric, "leave_one_component_out": ablations, "by_year": by_year, "by_code": by_code}


def market_regime(row):
    # PIT-compatible coarse regime based only on the same-day ETF cross-section.
    # This is descriptive conditioning, not a trading state or new rule.
    return row.get("date")


def add_date_regime(rows):
    grouped = defaultdict(list)
    for r in rows:
        if r.get("change_pct") is not None:
            grouped[r["date"]].append(r["change_pct"])
    date_median = {d: mean(v) for d, v in grouped.items()}
    for r in rows:
        m = date_median.get(r["date"])
        if m is None:
            r["market_regime"] = "UNKNOWN"
        elif m >= 1.0:
            r["market_regime"] = "BROAD_STRONG"
        elif m <= -1.0:
            r["market_regime"] = "BROAD_WEAK"
        else:
            r["market_regime"] = "BROAD_NEUTRAL"
    return rows


def regime_metrics(rows, signal):
    selected = [r for r in rows if signal_mask(r, signal)]
    return {reg: metric([r for r in selected if r.get("market_regime") == reg]) for reg in ["BROAD_STRONG", "BROAD_NEUTRAL", "BROAD_WEAK"]}


def fmt(x):
    return "NA" if x is None else f"{x:.3f}%"


def render(payload):
    labels = {
        "healthy_breakout": "健康突破",
        "volume_recovery": "放量修复",
        "consolidation_breakout": "整理突破",
        "tech_risk_appetite": "科技风险偏好改善",
        "consecutive_rise_risk_review": "连续上涨风险复核",
    }
    lines = [
        "# MASTER 6.2 组件消融与条件稳定性研究（自动生成）",
        "",
        "本轮冻结现行MASTER定义，只做leave-one-component-out消融，不搜索替代阈值、不按结果调参。",
        "",
    ]
    for key, label in labels.items():
        item = payload["signals"][key]
        b = item["baseline"]
        lines += [
            f"## {label}",
            "",
            f"Baseline：n={b['n']}，T+5={fmt(b['t5_mean'])}，相对ETF池={fmt(b['rel5_mean'])}，MAE={fmt(b['mae5_mean'])}，MFE={fmt(b['mfe5_mean'])}。",
            "",
            "|移除组件|样本|T+5|相对ETF池|相对baseline变化|",
            "|---|---:|---:|---:|---:|",
        ]
        for comp, m in item["leave_one_component_out"].items():
            lines.append(f"|{comp}|{m['n']}|{fmt(m['t5_mean'])}|{fmt(m['rel5_mean'])}|{fmt(m['delta_rel5_vs_baseline'])}|")
        lines += ["", "市场横截面状态条件化：", ""]
        for reg, m in item["market_regime"].items():
            lines.append(f"- {reg}: n={m['n']}, T+5={fmt(m['t5_mean'])}, 相对ETF池={fmt(m['rel5_mean'])}")
        lines.append("")
    lines += [
        "## 使用边界",
        "",
        "- 消融结果只回答某固定条件是否在当前完整定义中提供可观察增量，不证明因果，也不自动授权删除组件。",
        "- 样本明显扩张时，移除组件后的收益变化同时混入样本构成变化，因此后续还需做匹配/条件化研究。",
        "- `BROAD_STRONG/NEUTRAL/WEAK`只是研究条件标签，不进入MASTER、不成为市场状态或交易许可。",
        "- 连续上涨风险复核仍以风险缓释与赢家右尾的权衡为核心，不按T+5均值机械生成卖出。",
        "- 本轮不产生风险许可、Trial/Confirm、金额、卖出份额或订单。",
        "",
    ]
    return "\n".join(lines)


def main():
    by_code, dates = load_panel()
    rows = add_date_regime(attach_components(by_code))
    signals = {}
    for signal in [
        "healthy_breakout",
        "volume_recovery",
        "consolidation_breakout",
        "tech_risk_appetite",
        "consecutive_rise_risk_review",
    ]:
        item = evaluate_signal(rows, signal)
        item["market_regime"] = regime_metrics(rows, signal)
        signals[signal] = item

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_SECTION6_COMPONENT_ABLATION",
        "point_in_time": True,
        "parameter_optimization": False,
        "date_range": [dates[0], dates[-1]] if dates else [],
        "signals": signals,
        "decision_boundary": "Research-only component ablation; no trading authority.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "section6_component_ablation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT_DIR / "section6_component_ablation.md").write_text(render(payload) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "signals": {k: v["baseline"] for k, v in signals.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
