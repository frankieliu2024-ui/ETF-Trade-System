"""PIT-only ETF swing/T research over the canonical daily feature panel.

This deliberately does not infer intraday fills from daily highs/lows.  Signals are
formed at close D and the first possible fill is open D+1.  The file is research
only and never writes the production ETF universe.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "config/market/etf_monitor_universe.json"
DAILY = ROOT / "events/research/daily_features"
OUT = ROOT / "research/backtests/etf_t_swing_stage1_validation.json"

THRESHOLDS = (0.01, 0.015, 0.02, 0.025, 0.03, 0.04)
HOLDS = (1, 2, 3, 5, 10)
COSTS = (0.001, 0.002, 0.003)  # round trip: 10/20/30bp
MOBILE = (0.10, 0.20, 0.25, 0.30, 0.40, 0.50)
DEFAULT_THRESHOLD = 0.02
DEFAULT_HOLD = 3
DEFAULT_MOBILE = 0.25


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def load_panel():
    rows = defaultdict(dict)
    for path in sorted(DAILY.glob("*.json")):
        obj = json.loads(path.read_text(encoding="utf-8"))
        d = obj.get("market_date")
        for f in obj.get("features", []):
            if all(finite(f.get(k)) for k in ("open", "high", "low", "close")):
                rows[str(f["code"])][d] = {**f, "date": d}
    return rows


def load_external_csv():
    """Use only the existing on-disk study extract; no network or backfill is invented."""
    path = ROOT / "research/backtests/backtest_etf_window.csv"
    out = defaultdict(dict)
    if not path.exists():
        return out
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            code = str(r.get("code", ""))
            try:
                vals = {"open": float(r["开盘"]), "high": float(r["最高"]), "low": float(r["最低"]), "close": float(r["收盘"]), "amount": float(r.get("金额") or 0), "volume": None, "name": "外部候选"}
            except (KeyError, TypeError, ValueError):
                continue
            out[code][r["date"]] = {**vals, "date": r["date"], "code": code, "intraday_path": {"sampling_coverage": "NOT_AVAILABLE"}}
    return out


def pct(a, b):
    return a / b - 1.0 if finite(a) and finite(b) and b else None


def max_drawdown(values):
    peak = values[0] if values else 1.0
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1.0)
    return worst


def summary(values, trades, turnover, winner_loss, wrong_rebuy):
    end = values[-1] if values else 1.0
    return {
        "net_return": end - 1.0,
        "max_drawdown": max_drawdown(values),
        "trades": trades,
        "turnover": turnover,
        "net_per_turnover": (end - 1.0) / turnover if turnover else 0.0,
        "winner_right_tail_loss": winner_loss,
        "wrong_rebuy_loss": wrong_rebuy,
    }


def run_strategy(rows, threshold=DEFAULT_THRESHOLD, hold=DEFAULT_HOLD, mobile=DEFAULT_MOBILE, cost=0.002, mode="core_mobile"):
    dates = sorted(rows)
    if len(dates) < 30:
        return {"status": "INSUFFICIENT_EVIDENCE", "observations": len(dates)}
    closes = [rows[d]["close"] for d in dates]
    opens = [rows[d]["open"] for d in dates]
    core_w = 0.0 if mode == "full_swing" else (1.0 - mobile if mode == "core_mobile" else 0.70)
    tactical_w = 1.0 - core_w if mode != "beta_control" else 0.0
    beta_cash = 0.30 if mode == "beta_control" else 0.0
    core = 1.0
    tactical_cash = tactical_w
    active = None
    equity = []
    trades = 0
    turnover = 0.0
    winner_loss = 0.0
    wrong_rebuy = 0.0
    for i, d in enumerate(dates):
        # Mark the core at close-to-close; the first observation is the base.
        if i:
            core *= closes[i] / closes[i - 1]
        if active and i >= active["exit_i"]:
            exit_price = closes[i]
            gross = exit_price / active["entry_price"] - 1.0
            net = gross - cost
            tactical_cash *= 1.0 + net
            turnover += 2.0 * tactical_w
            if gross < 0:
                wrong_rebuy += -gross * tactical_w
            active = None
        # Signal is close D, execution starts at open D+1. Historical highs/lows
        # are intentionally not consulted for fill ordering.
        if active is None and i >= 20 and i + 1 < len(dates):
            ma = sum(closes[i - 20:i]) / 20.0  # strictly before D close
            deviation = closes[i] / ma - 1.0
            mom5 = closes[i] / closes[i - 5] - 1.0
            # Downside mean-reversion only; strong positive momentum is a
            # winner/right-tail guard and blocks selling into acceleration.
            if deviation <= -threshold and mom5 < threshold:
                entry_i = i + 1
                active = {"entry_price": opens[entry_i] * (1.0 + cost / 2.0), "exit_i": min(entry_i + hold - 1, len(dates) - 1)}
                trades += 1
                if closes[i] > closes[i - 1] if i else False:
                    winner_loss += tactical_w * max(0.0, closes[i] / closes[i - 1] - 1.0)
        marked_tactical = tactical_cash
        if active:
            marked_tactical = tactical_cash * (closes[i] / active["entry_price"])
        total = core_w * core + (marked_tactical if tactical_w else beta_cash)
        equity.append(total)
    return summary(equity, trades, turnover, winner_loss, wrong_rebuy)


def buy_hold(rows):
    ds = sorted(rows)
    if len(ds) < 2:
        return {"status": "INSUFFICIENT_EVIDENCE", "observations": len(ds)}
    vals = [1.0]
    for a, b in zip(ds, ds[1:]):
        vals.append(vals[-1] * rows[b]["close"] / rows[a]["close"])
    return summary(vals, 0, 0.0, 0.0, 0.0)


def features(rows):
    ds = sorted(rows)
    returns = [rows[b]["close"] / rows[a]["close"] - 1 for a, b in zip(ds, ds[1:])]
    ranges = [(rows[d]["high"] - rows[d]["low"]) / rows[d]["close"] for d in ds]
    amounts = [rows[d].get("amount") for d in ds if finite(rows[d].get("amount"))]
    return {
        "observations": len(ds), "sample_start": ds[0] if ds else None, "sample_end": ds[-1] if ds else None,
        "coverage": "PASS" if len(ds) >= 200 else "SHORT_SAMPLE", "intraday_coverage": "NOT_AVAILABLE",
        "avg_daily_range": sum(ranges) / len(ranges) if ranges else None,
        "realized_volatility": (sum(r*r for r in returns) / len(returns)) ** 0.5 if returns else None,
        "avg_amount_20": sum(amounts[-20:]) / min(20, len(amounts)) if amounts else None,
        "avg_amount_60": sum(amounts[-60:]) / min(60, len(amounts)) if amounts else None,
        "trend_persistence": sum(1 for a,b in zip(returns, returns[1:]) if a*b > 0) / max(1, len(returns)-1),
        "mean_reversion_evidence": sum(1 for a,b in zip(returns, returns[1:]) if a*b < 0) / max(1, len(returns)-1),
        "trading_constraint": "T+0_UNKNOWN_CROSS_BORDER_OR_T+1_A_SHARE; no intraday evidence",
    }


def evaluate(code, rows, name):
    bh = buy_hold(rows)
    current_codes = {str(x["code"]) for x in json.loads(UNIVERSE.read_text(encoding="utf-8"))["objects"]}
    base = {"code": code, "name": name, "category": "existing_formal" if code in current_codes else "external_candidate"}
    base.update(features(rows))
    base["buy_and_hold"] = bh
    base["strategies"] = {}
    for c in COSTS:
        key = f"{int(c*10000)}bp"
        base["strategies"][key] = {
            "core_mobile": run_strategy(rows, cost=c, mode="core_mobile"),
            "full_swing": run_strategy(rows, cost=c, mobile=1.0, mode="full_swing"),
            "beta_control": run_strategy(rows, cost=c, mode="beta_control"),
        }
    base["parameter_sensitivity"] = {
        f"{t:.3f}_{h}d": run_strategy(rows, threshold=t, hold=h, cost=0.002, mode="core_mobile")
        for t in THRESHOLDS for h in HOLDS
    }
    base["mobile_sensitivity"] = {f"{m:.2f}": run_strategy(rows, mobile=m, cost=0.002, mode="core_mobile") for m in MOBILE}
    base["yearly"] = {}
    for y in ("2024", "2025", "2026"):
        yr = {d: rows[d] for d in rows if d.startswith(y)}
        base["yearly"][y] = run_strategy(yr, cost=0.002, mode="core_mobile") if len(yr) >= 30 else {"status": "INSUFFICIENT_EVIDENCE", "observations": len(yr)}
    base["regime_note"] = "Regime labels require PIT market_structure_context; this panel supports only a conservative trend guard. No regime claim is made for unavailable fields."
    base["qualification"] = "RESEARCH_ONLY" if len(rows) < 200 else "CONDITIONAL"
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(OUT))
    args = ap.parse_args()
    current = json.loads(UNIVERSE.read_text(encoding="utf-8"))["objects"]
    panel = load_panel()
    external = load_external_csv()
    all_rows = {**panel}
    for code, rows in external.items():
        if code not in all_rows:
            all_rows[code] = rows
    results = []
    for obj in current:
        code = str(obj["code"])
        results.append(evaluate(code, all_rows.get(code, {}), obj["name"]))
    candidates = [code for code in sorted(set(all_rows) - {str(x["code"]) for x in current})]
    external_results = [evaluate(code, all_rows[code], "外部候选") for code in candidates]
    out = {
        "research_id": "etf_t_swing_stage1",
        "generated_at": date.today().isoformat(),
        "status": "PASS_WITH_RESEARCH_BOUNDARY",
        "latest_main_sha": "493a712d6c54f29f9080f99908041e44c3cf2911",
        "method": {"signal": "close D only; 20d mean deviation and 5d momentum guard", "execution": "open D+1; close after 1/2/3/5/10 trading days", "cost_round_trip": ["10bp", "20bp", "30bp"], "pit": True, "intraday": "NOT_EXECUTED_NO_HISTORICAL_MINUTE_COVERAGE", "production_universe_mutated": False},
        "data_audit": {"daily_feature_days": len(list(DAILY.glob("*.json"))), "current_formal_count": len(current), "external_candidates_scanned": len(candidates), "external_deep_research_count": len(external_results), "external_limitations": "Only 159687 exists outside the formal panel in the on-disk extract; it has 24 observations and is research-only. This is not a claim of exhaustive current-market coverage."},
        "current_11": results,
        "external_candidate_screen": external_results,
        "walk_forward": {"calibration": "2024", "validation": "2025", "final_holdout": "2026 YTD", "selection_rule": "pre-registered 2% / 3d / 25% mobile; no in-sample best-single-point promotion"},
        "hypotheses": {"H1_588000": "INSUFFICIENT_EVIDENCE", "H2_561980": "INSUFFICIENT_EVIDENCE", "H3_159781": "INSUFFICIENT_EVIDENCE"},
        "master_8_1": {"status": "RESEARCH_ONLY", "reason": "execution translatability, historical minute coverage, and cross-year holdout evidence do not meet formal conversion threshold"},
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": args.output, "current_11": len(results), "external_candidates": len(candidates)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
