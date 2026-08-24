from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
KRONOS_DIR = ROOT / "events/research/kronos_backfill"
DAILY_DIR = ROOT / "events/research/daily_features"
OUT_PATH = ROOT / "research/backtests/kronos_stage2_validation.json"


def round4(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def load_daily():
    by_code = defaultdict(list)
    for path in sorted(DAILY_DIR.glob("*.json")):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        d = str(p.get("market_date") or path.stem)
        for item in p.get("features") or []:
            code = str(item.get("code") or "")
            close = item.get("close")
            if code and close is not None:
                by_code[code].append((d, float(close)))
    for code in by_code:
        by_code[code].sort()
    return by_code


def build_momentum_map(by_code):
    out = {}
    for code, rows in by_code.items():
        for i, (d, close) in enumerate(rows):
            r5 = None if i < 5 else (close / rows[i-5][1] - 1) * 100
            r20 = None if i < 20 else (close / rows[i-20][1] - 1) * 100
            out[(code, d)] = {"mom5": r5, "mom20": r20}
    return out


def load_records(momentum):
    rows = []
    for path in sorted(KRONOS_DIR.glob("*.json")):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
            f = p.get("forecast") or {}
            o = p.get("realized_forward_outcome") or {}
            code = str(p.get("code") or "")
            d = str(p.get("market_date") or "")
            actual = o.get("terminal_return_pct")
            pred = f.get("forecast_return_median_pct")
            if not code or not d or actual is None or pred is None:
                continue
            m = momentum.get((code, d), {})
            rows.append({
                "code": code,
                "date": d,
                "bias": str(f.get("kronos_bias") or "unknown"),
                "pred": float(pred),
                "actual": float(actual),
                "mom5": m.get("mom5"),
                "mom20": m.get("mom20"),
            })
        except Exception:
            continue
    return rows


def direction_accuracy(rows, key):
    hits = []
    for r in rows:
        v = r.get(key)
        a = r.get("actual")
        if v is None or a is None or float(v) == 0 or float(a) == 0:
            continue
        hits.append((float(v) > 0) == (float(a) > 0))
    return round4(sum(hits) / len(hits)) if hits else None


def group_stats(rows):
    a = [r["actual"] for r in rows]
    return {
        "n": len(rows),
        "mean_forward_pct": round4(mean(a)) if a else None,
        "median_forward_pct": round4(median(a)) if a else None,
        "positive_rate": round4(sum(x > 0 for x in a) / len(a)) if a else None,
        "kronos_direction_accuracy": direction_accuracy(rows, "pred"),
        "mom5_direction_accuracy": direction_accuracy(rows, "mom5"),
        "mom20_direction_accuracy": direction_accuracy(rows, "mom20"),
    }


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    xr = np.argsort(np.argsort(x)).astype(float)
    yr = np.argsort(np.argsort(y)).astype(float)
    return float(np.corrcoef(xr, yr)[0, 1])


def bootstrap_spread(bull, bear, n_iter=2000, seed=20260824):
    if not bull or not bear:
        return None
    rng = np.random.default_rng(seed)
    b1 = np.asarray([r["actual"] for r in bull], dtype=float)
    b2 = np.asarray([r["actual"] for r in bear], dtype=float)
    vals = []
    for _ in range(n_iter):
        vals.append(float(np.mean(rng.choice(b1, len(b1), replace=True)) - np.mean(rng.choice(b2, len(b2), replace=True))))
    return {"p2_5": round4(np.percentile(vals, 2.5)), "p50": round4(np.percentile(vals, 50)), "p97_5": round4(np.percentile(vals, 97.5))}


def main():
    daily = load_daily()
    momentum = build_momentum_map(daily)
    rows = load_records(momentum)
    by_bias = defaultdict(list)
    by_code = defaultdict(list)
    by_regime = defaultdict(list)
    by_date = defaultdict(list)
    for r in rows:
        by_bias[r["bias"]].append(r)
        by_code[r["code"]].append(r)
        by_regime[r["date"][:7]].append(r)
        by_date[r["date"]].append(r)

    cross_k, cross_m5, cross_m20 = [], [], []
    cross_dates = 0
    for d, group in sorted(by_date.items()):
        if len(group) < 5:
            continue
        cross_dates += 1
        actual = [r["actual"] for r in group]
        k = spearman([r["pred"] for r in group], actual)
        m5rows = [r for r in group if r["mom5"] is not None]
        m20rows = [r for r in group if r["mom20"] is not None]
        if k is not None: cross_k.append(k)
        if len(m5rows) >= 5:
            v = spearman([r["mom5"] for r in m5rows], [r["actual"] for r in m5rows])
            if v is not None: cross_m5.append(v)
        if len(m20rows) >= 5:
            v = spearman([r["mom20"] for r in m20rows], [r["actual"] for r in m20rows])
            if v is not None: cross_m20.append(v)

    code_spreads = {}
    positive_code_spreads = 0
    eligible_code_spreads = 0
    for code, group in sorted(by_code.items()):
        bull = [r for r in group if r["bias"] == "bullish"]
        bear = [r for r in group if r["bias"] == "bearish"]
        spread = None
        if bull and bear:
            spread = mean([r["actual"] for r in bull]) - mean([r["actual"] for r in bear])
            eligible_code_spreads += 1
            if spread > 0:
                positive_code_spreads += 1
        code_spreads[code] = {**group_stats(group), "bullish_minus_bearish_mean_spread_pct_points": round4(spread)}

    bull = by_bias.get("bullish", [])
    bear = by_bias.get("bearish", [])
    neutral = by_bias.get("neutral", [])
    spread = mean([r["actual"] for r in bull]) - mean([r["actual"] for r in bear]) if bull and bear else None

    agree_bull = [r for r in rows if r["bias"] == "bullish" and r.get("mom5") is not None and r["mom5"] > 0]
    disagree_bull = [r for r in rows if r["bias"] == "bullish" and r.get("mom5") is not None and r["mom5"] <= 0]
    agree_bear = [r for r in rows if r["bias"] == "bearish" and r.get("mom5") is not None and r["mom5"] < 0]
    disagree_bear = [r for r in rows if r["bias"] == "bearish" and r.get("mom5") is not None and r["mom5"] >= 0]

    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "KRONOS_STAGE2_INCREMENTAL_VALIDATION",
        "total_evaluations": len(rows),
        "overall": group_stats(rows),
        "by_bias": {k: group_stats(v) for k, v in sorted(by_bias.items())},
        "bullish_minus_bearish_mean_spread_pct_points": round4(spread),
        "bullish_minus_bearish_bootstrap_95pct": bootstrap_spread(bull, bear),
        "cross_section_rank_ic": {
            "eligible_dates": cross_dates,
            "kronos_mean_spearman": round4(mean(cross_k)) if cross_k else None,
            "mom5_mean_spearman": round4(mean(cross_m5)) if cross_m5 else None,
            "mom20_mean_spearman": round4(mean(cross_m20)) if cross_m20 else None,
        },
        "agreement_with_price_only_baseline": {
            "kronos_bullish_and_mom5_positive": group_stats(agree_bull),
            "kronos_bullish_but_mom5_nonpositive": group_stats(disagree_bull),
            "kronos_bearish_and_mom5_negative": group_stats(agree_bear),
            "kronos_bearish_but_mom5_nonnegative": group_stats(disagree_bear),
        },
        "cross_etf_repeatability": {
            "eligible_etfs_with_both_bullish_and_bearish": eligible_code_spreads,
            "etfs_with_positive_bullish_minus_bearish_spread": positive_code_spreads,
            "positive_spread_share": round4(positive_code_spreads / eligible_code_spreads) if eligible_code_spreads else None,
            "by_code": code_spreads,
        },
        "by_month": {k: group_stats(v) for k, v in sorted(by_regime.items())},
        "baseline_boundary": {
            "baseline": "只使用当时已经可见的5日/20日价格动量作为简单非Kronos客观基线。",
            "master_baseline_reconstructed": False,
            "reason": "历史MASTER正式决策不得事后伪造；只有存在当时真实决策记录时才能比较MASTER+Kronos与MASTER-alone。"
        },
        "decision_eligible": False,
        "trade_signal": None,
        "research_review": {
            "sample_size_gate": len(rows) >= 500,
            "bias_order_gate": bool(bull and neutral and bear and mean([r['actual'] for r in bull]) > mean([r['actual'] for r in neutral]) > mean([r['actual'] for r in bear])),
            "spread_ci_above_zero_gate": bool((bootstrap_spread(bull, bear) or {}).get("p2_5") is not None and (bootstrap_spread(bull, bear) or {}).get("p2_5") > 0),
            "cross_etf_repeatability_gate": bool(eligible_code_spreads and positive_code_spreads / eligible_code_spreads >= 0.7),
            "rank_ic_increment_gate": bool(cross_k and ((not cross_m5) or mean(cross_k) > mean(cross_m5))),
            "conclusion_rule": "这些是研究审查证据，不是交易许可。只有多个独立维度共同支持，才考虑让Kronos进入正式研究摘要；仍不得直接修改MASTER或产生交易动作。"
        }
    }
    atomic_json_write(OUT_PATH, payload)
    print(json.dumps({"total": len(rows), "spread": round4(spread), "rank_ic": payload["cross_section_rank_ic"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
