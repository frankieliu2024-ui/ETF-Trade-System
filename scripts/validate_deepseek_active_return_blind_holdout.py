"""Deterministic blind-holdout validator for the frozen DeepSeek active-return hypotheses.

Research-only. This module never calls an LLM, never writes canonical state, never
creates a trade action, and never changes MASTER semantics. The first blind-test
feature definitions, thresholds and acceptance gates were frozen in #517 before
2026 outcomes were computed.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/research/active_return_stage1.json"
DAILY_DIR = ROOT / "events/research/daily_features"
BASELINE = ROOT / "research/backtests/active_return_stage1_validation.json"

HOLDOUT_START = "2026-01-01"
HOLDOUT_END = "2026-08-21"
HORIZONS = (5, 10, 20)
MIN_HOLDOUT_N = 15
ROUND_TRIP_COST = 0.002  # 20 bps
CONCENTRATION_LIMIT = 0.50
PARITY_TOL = 1e-6

FROZEN_HYPOTHESES = (
    {
        "hypothesis_id": "H1_breadth_regime_gate",
        "base_evidence": "active_migration_spread",
        "feature": "breadth_mom20_positive_ratio",
        "op": "gte",
        "threshold": 0.50,
    },
    {
        "hypothesis_id": "H2_dispersion_regime_gate",
        "base_evidence": "active_migration_spread",
        "feature": "cross_section_dispersion_mom20_pct",
        "op": "gte",
        "threshold": 0.02,
    },
    {
        "hypothesis_id": "H3_volatility_regime_gate",
        "base_evidence": "right_tail_holding",
        "feature": "median_20d_volatility_pct",
        "op": "lte",
        "threshold": 0.02,
    },
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def r6(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "positive_rate": None}
    return {
        "n": len(values),
        "mean": r6(mean(values)),
        "median": r6(median(values)),
        "positive_rate": r6(sum(v > 0 for v in values) / len(values)),
    }


def rank_desc(items: dict[str, float]) -> dict[str, int]:
    ordered = sorted(items.items(), key=lambda kv: kv[1], reverse=True)
    return {code: idx + 1 for idx, (code, _) in enumerate(ordered)}


def condition_pass(value: float, op: str, threshold: float) -> bool:
    if op == "gte":
        return value >= threshold
    if op == "gt":
        return value > threshold
    if op == "lte":
        return value <= threshold
    if op == "lt":
        return value < threshold
    raise ValueError(f"unsupported operator: {op}")


def load_daily_rows() -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    cfg = load_json(CFG)
    start = str(cfg["data"]["start_date"])
    end = str(cfg["data"]["end_date"])
    day_rows: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(DAILY_DIR.glob("*.json")):
        d = path.stem
        if d < start or d > end:
            continue
        payload = load_json(path)
        if not payload.get("historical_backfill") and payload.get("mode") != "OBJECTIVE_RESEARCH_FEATURES_HISTORICAL_BACKFILL":
            if payload.get("market_phase") != "CLOSED":
                continue
        rows: dict[str, dict[str, Any]] = {}
        for item in payload.get("features") or []:
            code = str(item.get("code") or "")
            o = finite(item.get("open"))
            c = finite(item.get("close"))
            if code and o is not None and c is not None and o > 0 and c > 0:
                rows[code] = {"open": o, "close": c, "name": item.get("name")}
        if rows:
            day_rows[d] = rows
    dates = sorted(day_rows)
    if len(dates) < 80:
        raise RuntimeError(f"insufficient daily history: {len(dates)}")
    return dates, day_rows


def build_signal_context(
    dates: list[str], day_rows: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    code_dates: dict[str, list[str]] = defaultdict(list)
    for d in dates:
        for code in day_rows[d]:
            code_dates[code].append(d)
    code_pos = {code: {d: i for i, d in enumerate(ds)} for code, ds in code_dates.items()}

    signals: dict[str, dict[str, Any]] = {}
    for d in dates:
        m5: dict[str, float] = {}
        m20: dict[str, float] = {}
        high20: dict[str, bool] = {}
        vol20: dict[str, float] = {}
        for code, row in day_rows[d].items():
            pos = code_pos.get(code, {}).get(d)
            ds = code_dates.get(code) or []
            if pos is None or pos < 20:
                continue
            c = row["close"]
            c5 = day_rows[ds[pos - 5]][code]["close"]
            c20 = day_rows[ds[pos - 20]][code]["close"]
            if c5 <= 0 or c20 <= 0:
                continue
            m5[code] = c / c5 - 1.0
            m20[code] = c / c20 - 1.0
            prior20_closes = [day_rows[x][code]["close"] for x in ds[pos - 19 : pos + 1]]
            high20[code] = c >= max(prior20_closes) - 1e-12
            daily_returns = []
            for j in range(pos - 19, pos + 1):
                current_close = day_rows[ds[j]][code]["close"]
                prior_close = day_rows[ds[j - 1]][code]["close"]
                daily_returns.append(current_close / prior_close - 1.0)
            vol20[code] = pstdev(daily_returns)

        common = set(m5) & set(m20) & set(vol20)
        if len(common) < 5:
            continue
        r5 = rank_desc({c: m5[c] for c in common})
        r20 = rank_desc({c: m20[c] for c in common})
        n = len(common)
        leaders = [c for c in common if r5[c] <= 3 and r20[c] <= 3 and m5[c] > 0 and m20[c] > 0]
        laggards = [c for c in common if r5[c] >= n - 2 and r20[c] >= n - 2]
        new_high_leaders = [c for c in leaders if high20.get(c)]
        m20_values = [m20[c] for c in common]
        vol20_values = [vol20[c] for c in common]
        regime = {
            "breadth_mom20_positive_ratio": sum(m20[c] > 0 for c in common) / len(common),
            "cross_section_dispersion_mom20_pct": pstdev(m20_values),
            "median_20d_volatility_pct": median(vol20_values),
        }
        signals[d] = {
            "n": n,
            "leaders": sorted(leaders),
            "laggards": sorted(laggards),
            "new_high_leaders": sorted(new_high_leaders),
            "mom20": {c: m20[c] for c in common},
            "regime": regime,
        }
    return signals


def forward_return(
    signal_date: str,
    code: str,
    horizon: int,
    dates: list[str],
    day_rows: dict[str, dict[str, dict[str, Any]]],
    date_idx: dict[str, int],
) -> float | None:
    i = date_idx[signal_date]
    if i + 1 >= len(dates) or i + horizon >= len(dates):
        return None
    entry_d = dates[i + 1]
    exit_d = dates[i + horizon]
    if code not in day_rows.get(entry_d, {}) or code not in day_rows.get(exit_d, {}):
        return None
    entry = day_rows[entry_d][code]["open"]
    exit_c = day_rows[exit_d][code]["close"]
    return exit_c / entry - 1.0 if entry > 0 else None


def calculate_holdout_evidence(
    dates: list[str],
    day_rows: dict[str, dict[str, dict[str, Any]]],
    signals: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    date_idx = {d: i for i, d in enumerate(dates)}
    holdout_dates = [d for d in sorted(signals) if HOLDOUT_START <= d <= HOLDOUT_END]
    universe_median: dict[tuple[str, int], float] = {}
    for d in holdout_dates:
        sig = signals[d]
        for h in HORIZONS:
            vals = []
            for code in sig["mom20"]:
                rr = forward_return(d, code, h, dates, day_rows, date_idx)
                if rr is not None:
                    vals.append(rr)
            if vals:
                universe_median[(d, h)] = median(vals)

    baseline: dict[str, Any] = {"active_migration_spread": {}, "right_tail_holding": {}}
    per_date: dict[str, dict[int, dict[str, Any]]] = {d: {} for d in holdout_dates}

    for d in holdout_dates:
        sig = signals[d]
        for h in HORIZONS:
            med_fwd = universe_median.get((d, h))
            migration_value = None
            leaders_ret = [forward_return(d, c, h, dates, day_rows, date_idx) for c in sig["leaders"]]
            laggards_ret = [forward_return(d, c, h, dates, day_rows, date_idx) for c in sig["laggards"]]
            leaders_ret = [x for x in leaders_ret if x is not None]
            laggards_ret = [x for x in laggards_ret if x is not None]
            if leaders_ret and laggards_ret:
                migration_value = mean(leaders_ret) - mean(laggards_ret)

            right_tail_events: list[tuple[str, float]] = []
            if med_fwd is not None:
                for code in sig["new_high_leaders"]:
                    rr = forward_return(d, code, h, dates, day_rows, date_idx)
                    if rr is not None:
                        right_tail_events.append((code, rr - med_fwd))

            per_date[d][h] = {
                "migration_value": migration_value,
                "migration_participants": sorted(set(sig["leaders"] + sig["laggards"])) if migration_value is not None else [],
                "right_tail_events": right_tail_events,
            }

    for h in HORIZONS:
        mig_values = [per_date[d][h]["migration_value"] for d in holdout_dates if per_date[d][h]["migration_value"] is not None]
        right_values = [value for d in holdout_dates for _, value in per_date[d][h]["right_tail_events"]]
        baseline["active_migration_spread"][str(h)] = summarize(mig_values)
        baseline["right_tail_holding"][str(h)] = summarize(right_values)

    return {
        "holdout_signal_dates": holdout_dates,
        "baseline": baseline,
        "per_date": per_date,
    }


def concentration_share(counter: Counter[str]) -> float | None:
    total = sum(counter.values())
    if total <= 0:
        return None
    return max(counter.values()) / total


def validate_baseline_parity(calculated: dict[str, Any], persisted: dict[str, Any]) -> dict[str, Any]:
    failures = []
    details = []
    for base in ("active_migration_spread", "right_tail_holding"):
        for h in HORIZONS:
            calc = calculated[base][str(h)]
            expected = ((persisted.get(base) or {}).get(str(h), {}).get("folds") or {}).get("fold3") or {}
            item = {"base_evidence": base, "horizon": h, "calculated": calc, "persisted_fold3": expected}
            ok = calc.get("n") == expected.get("n")
            for key in ("mean", "median", "positive_rate"):
                a, b = calc.get(key), expected.get(key)
                if a is None or b is None:
                    ok = ok and a is None and b is None
                else:
                    ok = ok and abs(float(a) - float(b)) <= PARITY_TOL
            item["pass"] = ok
            details.append(item)
            if not ok:
                failures.append(f"{base}:{h}")
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "details": details}


def evaluate_hypothesis(
    hypothesis: dict[str, Any],
    holdout: dict[str, Any],
    signals: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    selected_dates = [
        d for d in holdout["holdout_signal_dates"]
        if condition_pass(signals[d]["regime"][hypothesis["feature"]], hypothesis["op"], hypothesis["threshold"])
    ]
    horizon_results = []
    horizons_with_n15 = 0
    passed_horizons = 0

    for h in HORIZONS:
        baseline = holdout["baseline"][hypothesis["base_evidence"]][str(h)]
        asset_counts: Counter[str] = Counter()
        if hypothesis["base_evidence"] == "active_migration_spread":
            values = []
            for d in selected_dates:
                entry = holdout["per_date"][d][h]
                if entry["migration_value"] is not None:
                    values.append(entry["migration_value"])
                    asset_counts.update(entry["migration_participants"])
            filtered = summarize(values)
            filtered_net = r6((filtered["mean"] - ROUND_TRIP_COST) if filtered["mean"] is not None else None)
            baseline_net = r6((baseline["mean"] - ROUND_TRIP_COST) if baseline["mean"] is not None else None)
            concentration = concentration_share(asset_counts)
            enough = filtered["n"] >= MIN_HOLDOUT_N
            if enough:
                horizons_with_n15 += 1
            strictly_better = False
            if filtered_net is not None and baseline_net is not None and filtered["positive_rate"] is not None and baseline["positive_rate"] is not None:
                strictly_better = filtered_net > baseline_net + 1e-12 or filtered["positive_rate"] > baseline["positive_rate"] + 1e-12
            gate = bool(
                enough and
                filtered_net is not None and filtered_net > 0.002 and
                filtered["positive_rate"] is not None and filtered["positive_rate"] > 0.52 and
                baseline_net is not None and filtered_net >= baseline_net - 1e-12 and
                baseline["positive_rate"] is not None and filtered["positive_rate"] >= baseline["positive_rate"] - 1e-12 and
                strictly_better and
                concentration is not None and concentration <= CONCENTRATION_LIMIT
            )
            result = {
                "horizon": h,
                "baseline": {**baseline, "mean_net_after_20bps": baseline_net},
                "filtered": {**filtered, "mean_net_after_20bps": filtered_net},
                "selected_signal_dates": len(selected_dates),
                "max_single_asset_participation_share": r6(concentration),
                "strictly_better": strictly_better,
                "blind_gate_pass": gate,
            }
        else:
            events: list[tuple[str, float]] = []
            for d in selected_dates:
                events.extend(holdout["per_date"][d][h]["right_tail_events"])
            values = [value for _, value in events]
            asset_counts.update(code for code, _ in events)
            filtered = summarize(values)
            concentration = concentration_share(asset_counts)
            enough = filtered["n"] >= MIN_HOLDOUT_N
            if enough:
                horizons_with_n15 += 1
            strictly_better = False
            if filtered["mean"] is not None and baseline["mean"] is not None and filtered["positive_rate"] is not None and baseline["positive_rate"] is not None:
                strictly_better = filtered["mean"] > baseline["mean"] + 1e-12 or filtered["positive_rate"] > baseline["positive_rate"] + 1e-12
            gate = bool(
                enough and
                filtered["mean"] is not None and filtered["mean"] > 0 and
                filtered["positive_rate"] is not None and filtered["positive_rate"] >= 0.50 and
                baseline["mean"] is not None and filtered["mean"] >= baseline["mean"] - 1e-12 and
                baseline["positive_rate"] is not None and filtered["positive_rate"] >= baseline["positive_rate"] - 1e-12 and
                strictly_better and
                concentration is not None and concentration <= CONCENTRATION_LIMIT
            )
            result = {
                "horizon": h,
                "baseline": baseline,
                "filtered": filtered,
                "selected_signal_dates": len(selected_dates),
                "max_single_asset_event_share": r6(concentration),
                "strictly_better": strictly_better,
                "blind_gate_pass": gate,
            }
        if gate:
            passed_horizons += 1
        horizon_results.append(result)

    if horizons_with_n15 < 2:
        classification = "INSUFFICIENT_HOLDOUT"
    elif passed_horizons >= 2:
        classification = "BLIND_SUPPORTED"
    else:
        classification = "BLIND_NOT_SUPPORTED"

    return {
        "hypothesis_id": hypothesis["hypothesis_id"],
        "base_evidence": hypothesis["base_evidence"],
        "condition": {"feature": hypothesis["feature"], "op": hypothesis["op"], "threshold": hypothesis["threshold"]},
        "selected_signal_dates": len(selected_dates),
        "condition_coverage": r6(len(selected_dates) / len(holdout["holdout_signal_dates"])) if holdout["holdout_signal_dates"] else None,
        "horizons_with_n15": horizons_with_n15,
        "passed_horizons": passed_horizons,
        "classification": classification,
        "horizons": horizon_results,
    }


def run_validation() -> dict[str, Any]:
    dates, day_rows = load_daily_rows()
    signals = build_signal_context(dates, day_rows)
    holdout = calculate_holdout_evidence(dates, day_rows, signals)
    persisted = load_json(BASELINE)
    parity = validate_baseline_parity(holdout["baseline"], persisted)
    if parity["status"] != "PASS":
        return {
            "schema_version": "1.0",
            "status": "INVALID_BASELINE_PARITY",
            "research_only": True,
            "holdout": [HOLDOUT_START, HOLDOUT_END],
            "baseline_parity": parity,
            "hypotheses": [],
            "any_blind_supported": False,
            "production_integration": False,
            "trade_signal": None,
            "master_override": False,
        }

    hypotheses = [evaluate_hypothesis(h, holdout, signals) for h in FROZEN_HYPOTHESES]
    return {
        "schema_version": "1.0",
        "status": "PASS",
        "research_only": True,
        "holdout": [HOLDOUT_START, HOLDOUT_END],
        "holdout_signal_date_count": len(holdout["holdout_signal_dates"]),
        "feature_semantics": {
            "dispersion_ddof": 0,
            "volatility_ddof": 0,
            "return_units": "decimal_fraction",
            "minimum_holdout_n": MIN_HOLDOUT_N,
            "single_asset_concentration_limit": CONCENTRATION_LIMIT,
            "active_migration_round_trip_cost": ROUND_TRIP_COST,
        },
        "baseline_parity": parity,
        "hypotheses": hypotheses,
        "any_blind_supported": any(h["classification"] == "BLIND_SUPPORTED" for h in hypotheses),
        "production_integration": False,
        "trade_signal": None,
        "master_override": False,
        "second_deepseek_call": False,
    }


def main() -> int:
    print(json.dumps(run_validation(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
