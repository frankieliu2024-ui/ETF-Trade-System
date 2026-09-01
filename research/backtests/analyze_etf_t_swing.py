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


def summary(values, trades, turnover, winner_loss, wrong_rebuy, events=None, initial_exposure=None, initial_mobile_state=None):
    end = values[-1] if values else 1.0
    return {
        "net_return": end - 1.0,
        "max_drawdown": max_drawdown(values),
        "trades": trades,
        "turnover": turnover,
        "net_per_turnover": (end - 1.0) / turnover if turnover else 0.0,
        "winner_right_tail_loss": winner_loss,
        "wrong_rebuy_loss": wrong_rebuy,
        "events": events or [],
        "initial_total_exposure": initial_exposure,
        "initial_mobile_state": initial_mobile_state,
    }


def transition_mobile(state, action):
    """Pure inventory state machine; mobile is never both shares and cash."""
    if action == "release":
        if state != "MOBILE_INVESTED":
            raise ValueError("cannot release mobile cash")
        return "MOBILE_CASH"
    if action == "rebuy":
        if state != "MOBILE_CASH":
            raise ValueError("cannot rebuy invested mobile")
        return "MOBILE_INVESTED"
    raise ValueError("unknown mobile action")


def run_strategy(rows, threshold=DEFAULT_THRESHOLD, hold=DEFAULT_HOLD, mobile=DEFAULT_MOBILE, cost=0.002, mode="core_mobile", family="trend_filter"):
    dates = sorted(rows)
    if len(dates) < 30:
        return {"status": "INSUFFICIENT_EVIDENCE", "observations": len(dates)}
    closes = [rows[d]["close"] for d in dates]
    opens = [rows[d]["open"] for d in dates]
    if mode == "beta_control":
        core_w, mobile_w = 0.70, 0.0
    elif mode == "full_swing":
        core_w, mobile_w = 0.0, 1.0
    else:
        core_w, mobile_w = 1.0 - mobile, mobile
    first = closes[0]
    core_units = core_w / first
    mobile_units = mobile_w / first
    mobile_cash = 0.0
    mobile_state = "MOBILE_INVESTED" if mobile_w else "NONE"
    initial_mobile_state = mobile_state
    initial_exposure = core_units * first + mobile_units * first + mobile_cash
    pending = None
    equity = []
    trades = 0
    turnover = 0.0
    winner_loss = 0.0
    wrong_rebuy = 0.0
    rebuy_anchor = None
    events = []
    for i, d in enumerate(dates):
        if pending and i == pending["exec_i"] and mobile_state != "NONE":
            px = opens[i]
            if pending["action"] == "release" and mobile_state == "MOBILE_INVESTED":
                value = mobile_units * px
                fee = value * cost / 2.0
                mobile_cash += value - fee
                mobile_units = 0.0
                mobile_state = transition_mobile(mobile_state, "release")
                rebuy_anchor = None
                trades += 1; turnover += value
                events.append({"date": d, "exec_i": i, "action": "release", "price": px, "fee": fee, "mobile_state": mobile_state, "cash_after": mobile_cash})
            elif pending["action"] == "rebuy" and mobile_state == "MOBILE_CASH":
                fee = mobile_cash * cost / 2.0
                mobile_units = max(0.0, (mobile_cash - fee) / px)
                mobile_cash = 0.0
                mobile_state = transition_mobile(mobile_state, "rebuy")
                rebuy_anchor = {"exec_i": i, "price": px}
                trades += 1; turnover += mobile_units * px
                events.append({"date": d, "exec_i": i, "action": "rebuy", "price": px, "fee": fee, "mobile_state": mobile_state, "mobile_units": mobile_units})
            pending = None
        total = core_units * closes[i] + mobile_units * closes[i] + mobile_cash
        equity.append(total)
        # Signal at close D; action can only execute at open D+1.
        if i >= 20 and i + 1 < len(dates) and mobile_state != "NONE":
            ma = sum(closes[i - 20:i]) / 20.0
            deviation = closes[i] / ma - 1.0
            mom5 = closes[i] / closes[i - 5] - 1.0
            vol = math.sqrt(sum((closes[k] / closes[k - 1] - 1.0) ** 2 for k in range(i - 19, i + 1)) / 20)
            effective = threshold if family in {"fixed", "mean_deviation", "no_trend_filter", "trend_filter"} else threshold * max(vol / 0.02, 0.5)
            trend_guard = family == "trend_filter"
            release_ok = deviation >= effective and (not trend_guard or mom5 <= threshold)
            rebuy_ok = deviation <= -effective and mom5 < threshold
            if mobile_state == "MOBILE_INVESTED" and release_ok:
                pending = {"exec_i": i + 1, "action": "release", "signal_date": d}
            elif mobile_state == "MOBILE_CASH" and rebuy_ok:
                pending = {"exec_i": i + 1, "action": "rebuy", "signal_date": d}
        if mobile_state == "MOBILE_CASH" and i > 20:
            # Opportunity loss is measured only after release, against holding
            # the mobile inventory throughout the same interval.
            last_release = next((e for e in reversed(events) if e["action"] == "release"), None)
            if last_release:
                winner_loss = max(winner_loss, mobile_w * max(0.0, closes[i] / last_release["price"] - 1.0))
        if mobile_state == "MOBILE_INVESTED" and rebuy_anchor and i > rebuy_anchor["exec_i"]:
            wrong_rebuy = max(wrong_rebuy, mobile_w * max(0.0, 1.0 - closes[i] / rebuy_anchor["price"]))
    if mobile_state == "MOBILE_CASH" and events:
        # A final open position in cash is still a valid release; no artificial
        # end-of-sample rebuy is invented.
        pass
    return summary(equity, trades, turnover, winner_loss, wrong_rebuy, events, initial_exposure, initial_mobile_state)


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
    base = {
        "code": code,
        "name": name,
        "category": "existing_formal" if code in current_codes else "external_candidate",
        "listed_date": "UNKNOWN_CANONICAL_PANEL_FIELD",
        "etf_type": "UNKNOWN_CANONICAL_PANEL_FIELD",
        "t0_t1": "UNKNOWN_REQUIRES_PIT_INSTRUMENT_METADATA",
        "liquidity_proxy": "amount_and_daily_range_only",
        "sample_length_penalty": "none" if len(rows) >= 200 else "SHORT_SAMPLE",
    }
    base.update(features(rows))
    base["buy_and_hold"] = bh
    base["strategies"] = {}
    for c in COSTS:
        key = f"{int(c*10000)}bp"
        base["strategies"][key] = {
            "core_mobile": run_strategy(rows, cost=c, mode="core_mobile", family="trend_filter"),
            "fixed_release_rebuy": run_strategy(rows, cost=c, mode="core_mobile", family="fixed"),
            "volatility_release_rebuy": run_strategy(rows, cost=c, mode="core_mobile", family="volatility"),
            "mean_deviation_release_rebuy": run_strategy(rows, cost=c, mode="core_mobile", family="mean_deviation"),
            "no_trend_filter": run_strategy(rows, cost=c, mode="core_mobile", family="no_trend_filter"),
            "full_swing": run_strategy(rows, cost=c, mobile=1.0, mode="full_swing", family="trend_filter"),
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
        if len(yr) >= 30:
            strat = run_strategy(yr, cost=0.002, mode="core_mobile", family="trend_filter")
            strat["relative_to_buy_and_hold_alpha"] = strat["net_return"] - buy_hold(yr)["net_return"]
            base["yearly"][y] = strat
        else:
            base["yearly"][y] = {"status": "INSUFFICIENT_EVIDENCE", "observations": len(yr)}
    base["accounting_contract"] = {"initial_total_etf_exposure": 1.0, "initial_core_exposure": 1.0 - DEFAULT_MOBILE, "initial_mobile_exposure": DEFAULT_MOBILE, "mobile_initial_state": "MOBILE_INVESTED", "no_leverage": True}
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
        "status": "RESEARCH_COMPLETE_EXTERNAL_MARKET_SCREEN_INCOMPLETE_DATA_LIMITATION",
        "latest_main_sha": "f00b0d7f5e56d257552fc514db948c56cf96fb1d",
        "method": {"signal": "close D only; 20d mean deviation and 5d momentum guard", "execution": "open D+1; close after 1/2/3/5/10 trading days", "cost_round_trip": ["10bp", "20bp", "30bp"], "pit": True, "intraday": "NOT_EXECUTED_NO_HISTORICAL_MINUTE_COVERAGE", "production_universe_mutated": False},
        "data_audit": {"daily_feature_days": len(list(DAILY.glob("*.json"))), "current_formal_count": len(current), "external_candidates_scanned": len(candidates), "external_deep_research_count": len(external_results), "external_limitations": "Only 159687 exists outside the formal panel in the on-disk extract; it has 24 observations and is research-only. This is not a claim of exhaustive current-market coverage."},
        "current_11": results,
        "external_candidate_screen": external_results,
        "walk_forward": {"calibration": "2024", "validation": "2025", "final_holdout": "2026 YTD", "selection_rule": "pre-registered 2% / 3d / 25% mobile; no in-sample best-single-point promotion"},
        "hypotheses": {"H1_588000": "REJECTED", "H2_561980": "INSUFFICIENT_EVIDENCE", "H3_159781": "REJECTED"},
        "master_8_1": {"status": "RESEARCH_ONLY", "reason": "execution translatability, historical minute coverage, and cross-year holdout evidence do not meet formal conversion threshold"},
        "overall_qualification": "FAIL_RESEARCH_ONLY",
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": args.output, "current_11": len(results), "external_candidates": len(candidates)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
