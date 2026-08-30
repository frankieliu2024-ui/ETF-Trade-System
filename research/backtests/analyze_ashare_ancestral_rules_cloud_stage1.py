#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research" / "backtests" / "revalidate_section6_baselines.py"
OUT = ROOT / "research" / "reports" / "generated" / "section6_revalidation"

spec = importlib.util.spec_from_file_location("section6_base", BASE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

HORIZONS = (1, 3, 5, 10)
OLD_KEYS = (
    "healthy_breakout",
    "volume_recovery",
    "consolidation_breakout",
    "tech_risk_appetite",
    "consecutive_rise_risk_review",
)

# All thresholds below are coarse pre-registered research buckets. They are not
# optimized against outcomes and are not trading-rule thresholds.

def weekday(d: str) -> int:
    return date.fromisoformat(d).weekday()  # Monday=0, Thursday=3


def clv(row: dict):
    return row.get("clv")


def amount_ratio(rows, i):
    return mod.amount_ratio(rows, i, 20, include_current=False)


def prior_ret(rows, i, n):
    return mod.ret_n(rows, i, n)


def pct(a, b):
    return mod.pct(a, b)


def consecutive_sign(rows, i, n, positive: bool):
    if i < n:
        return False
    vals = [rows[j].get("change_pct") for j in range(i - n, i)]
    if any(v is None for v in vals):
        return False
    return all(v > 0 for v in vals) if positive else all(v < 0 for v in vals)


def signal_map(rows, i):
    r = rows[i]
    prev = rows[i - 1] if i > 0 else None
    open_ret = pct(r.get("open"), r.get("prev_close"))
    close_ret = r.get("change_pct")
    hi_ret = pct(r.get("high"), r.get("prev_close"))
    lo_ret = pct(r.get("low"), r.get("prev_close"))
    c = clv(r)
    amt = amount_ratio(rows, i)
    prev_ret = prev.get("change_pct") if prev else None
    close_vs_open = pct(r.get("close"), r.get("open"))
    three_ret = prior_ret(rows, i, 3)

    thursday = weekday(r["date"]) == 3
    monday = weekday(r["date"]) == 0

    out = {
        # A. calendar / session proxies available from daily bars
        "black_thursday_unconditional": thursday,
        "black_thursday_after_3d_rise": bool(thursday and three_ret is not None and three_ret >= 3.0),
        "monday_after_prior_loss": bool(monday and prev_ret is not None and prev_ret <= -1.0),

        # B. opening structure
        "gap_up_fade": bool(open_ret is not None and open_ret >= 1.0 and close_vs_open is not None and close_vs_open < 0 and c is not None and c <= 0.40),
        "gap_up_hold": bool(open_ret is not None and open_ret >= 1.0 and close_vs_open is not None and close_vs_open >= 0 and c is not None and c >= 0.70),
        "gap_down_recovery": bool(open_ret is not None and open_ret <= -1.0 and close_vs_open is not None and close_vs_open > 0 and c is not None and c >= 0.70),
        "gap_down_fail": bool(open_ret is not None and open_ret <= -1.0 and close_vs_open is not None and close_vs_open <= 0 and c is not None and c <= 0.30),
        "gap_up_filled_intraday": bool(open_ret is not None and open_ret >= 1.0 and r.get("low") is not None and r.get("prev_close") is not None and r["low"] <= r["prev_close"]),
        "gap_down_filled_intraday": bool(open_ret is not None and open_ret <= -1.0 and r.get("high") is not None and r.get("prev_close") is not None and r["high"] >= r["prev_close"]),

        # C. daily-OHLC proxies for intraday path; true minute validation is a later stage
        "intraday_spike_fade_proxy": bool(hi_ret is not None and hi_ret >= 3.0 and close_ret is not None and hi_ret - close_ret >= 2.0 and c is not None and c <= 0.30),
        "intraday_dip_recovery_proxy": bool(lo_ret is not None and lo_ret <= -3.0 and close_ret is not None and close_ret - lo_ret >= 2.0 and c is not None and c >= 0.70),
        "large_range_high_close_proxy": bool(hi_ret is not None and lo_ret is not None and hi_ret - lo_ret >= 3.0 and c is not None and c >= 0.80),
        "large_range_low_close_proxy": bool(hi_ret is not None and lo_ret is not None and hi_ret - lo_ret >= 3.0 and c is not None and c <= 0.20),

        # D. participation / price quality
        "volume_expand_weak_close": bool(amt is not None and amt >= 1.2 and close_ret is not None and close_ret > 0 and c is not None and c <= 0.35),
        "volume_expand_strong_close": bool(amt is not None and amt >= 1.2 and close_ret is not None and close_ret > 0 and c is not None and c >= 0.75),
        "low_participation_rise": bool(amt is not None and amt < 0.8 and close_ret is not None and close_ret >= 1.0),

        # E/F. consecutive / extreme-state transitions
        "three_down_first_positive_day": bool(consecutive_sign(rows, i, 3, False) and close_ret is not None and close_ret > 0),
        "three_up_weak_close_day": bool(consecutive_sign(rows, i, 3, True) and close_ret is not None and c is not None and c <= 0.35),
        "big_down_next_day_repair": bool(prev_ret is not None and prev_ret <= -3.0 and close_ret is not None and close_ret > 0 and c is not None and c >= 0.60),
        "big_up_next_day_gap_fade": bool(prev_ret is not None and prev_ret >= 3.0 and open_ret is not None and open_ret >= 1.0 and close_vs_open is not None and close_vs_open < 0 and c is not None and c <= 0.40),
    }
    return out


def stats(rows, h, key="return_pct"):
    vals = [r["future"][h].get(key) for r in rows if h in r.get("future", {}) and r["future"][h].get(key) is not None]
    return mod.stats(vals)


def rel_stats(rows, h):
    vals = [r["future"][h].get("relative_to_pool_median_pct_points") for r in rows if h in r.get("future", {}) and r["future"][h].get("relative_to_pool_median_pct_points") is not None]
    return mod.stats(vals)


def grouped(rows, field, h=5):
    buckets = defaultdict(list)
    for r in rows:
        buckets[str(r[field])].append(r)
    return {
        k: {
            "count": len(v),
            "absolute": stats(v, h),
            "relative": rel_stats(v, h),
            "mae": stats(v, h, "mae_pct"),
            "mfe": stats(v, h, "mfe_pct"),
        }
        for k, v in sorted(buckets.items())
    }


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    row_index = {(r["date"], r["code"]): r for r in panel}

    signals_by_key = defaultdict(list)
    for code, rows in by_code.items():
        for i, base_row in enumerate(rows):
            full = row_index.get((base_row["date"], code))
            if full is None:
                continue
            full["year"] = full["date"][:4]
            sm = signal_map(rows, i)
            full["ancestral_signals"] = sm
            for key, hit in sm.items():
                if hit:
                    signals_by_key[key].append(full)

    signal_summary = {}
    for key, rows in sorted(signals_by_key.items()):
        overlap = sum(any(r.get("signals", {}).get(k) for k in OLD_KEYS) for r in rows)
        item = {
            "count": len(rows),
            "old_6_2_overlap_count": overlap,
            "old_6_2_overlap_share": round(overlap / len(rows), 4) if rows else 0.0,
            "horizons": {},
            "by_year_t5": grouped(rows, "year", 5),
            "by_code_t5": grouped(rows, "code", 5),
        }
        for h in HORIZONS:
            item["horizons"][str(h)] = {
                "absolute": stats(rows, h),
                "relative": rel_stats(rows, h),
                "mae": stats(rows, h, "mae_pct"),
                "mfe": stats(rows, h, "mfe_pct"),
            }
        signal_summary[key] = item

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_ASHARE_ANCESTRAL_RULES_CLOUD_STAGE1",
        "issue": 107,
        "date_range": [dates[0], dates[-1]],
        "panel_rows": len(panel),
        "candidate_scope": "Only hypotheses objectively testable from existing daily PIT OHLCV panel. Intraday/minute-only hypotheses are intentionally deferred.",
        "threshold_boundary": "Coarse pre-registered research buckets; no outcome optimization; not MASTER thresholds or trading rules.",
        "signals": signal_summary,
        "decision_boundary": "Research screening only. No risk permission, Trial/Confirm, amount, sell quantity, ranking, or order may be generated from this artifact.",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ashare_ancestral_rules_cloud_stage1.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# A股祖训全集重建：云端日线PIT Stage1（自动生成）",
        "",
        f"覆盖：{dates[0]} 至 {dates[-1]}；ETF×交易日={len(panel)}。",
        "",
        "本阶段只验证现有daily_features可客观重建的祖训候选。上午/下午、V形、尾盘、分钟承接等必须等日内/分钟覆盖审计后再做，不能用日K代理冒充。",
        "",
        "|候选|样本|T+1相对池|T+3相对池|T+5相对池|T+10相对池|T+5 MAE|T+5 MFE|与旧6.2重叠|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, z in signal_summary.items():
        def m(h, typ="relative"):
            x = z["horizons"][str(h)][typ].get("mean")
            return "NA" if x is None else f"{x:.3f}%"
        lines.append(
            f"|{key}|{z['count']}|{m(1)}|{m(3)}|{m(5)}|{m(10)}|{m(5,'mae')}|{m(5,'mfe')}|{z['old_6_2_overlap_share']:.1%}|"
        )
    lines += [
        "",
        "## Stage1筛选边界",
        "",
        "1. 单纯无条件星期效应即使显著，也必须继续做条件化和年度稳定性，不能直接形成祖训规则。",
        "2. OHLC只能为日内结构提供粗代理；涉及上午/下午、15/30/60分钟守住率、二次上攻、尾盘行为的候选必须转入云端日内/分钟Stage2。",
        "3. 与旧MASTER 6.2五项高度重叠且没有额外条件增量的候选优先合并/停止，不创造平行证据。",
        "4. 下一阶段只保留样本足够、年度/对象不是单点集中、并可能改善Trial/Confirm时机、金额、卖出份额或下一单位资本选择的候选。",
        "5. 本研究不以提高胜率为目标，不按结果调阈值。",
    ]
    (OUT / "ashare_ancestral_rules_cloud_stage1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "issue": 107, "candidate_count": len(signal_summary), "panel_rows": len(panel)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
