from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, median

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CFG = ROOT / "config/research/active_return_stage1.json"
DAILY_DIR = ROOT / "events/research/daily_features"
OUT = ROOT / "research/backtests/active_return_stage1_validation.json"
STATUS = ROOT / "data/state/active_return_research_status.json"
BJ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finite(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def r6(x):
    return round(float(x), 6) if x is not None and math.isfinite(float(x)) else None


def summarize(values: list[float]) -> dict:
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
    return {code: i + 1 for i, (code, _) in enumerate(ordered)}


def in_fold(d: str, fold: dict) -> bool:
    return fold["start"] <= d <= fold["end"]


def main() -> int:
    cfg = load_json(CFG)
    start = cfg["data"]["start_date"]
    end = cfg["data"]["end_date"]
    horizons = [int(x) for x in cfg["horizons"]]
    folds = cfg["folds"]

    # Load only objective historical daily facts. No historical decisions are reconstructed.
    day_rows: dict[str, dict[str, dict]] = {}
    for path in sorted(DAILY_DIR.glob("*.json")):
        d = path.stem
        if d < start or d > end:
            continue
        payload = load_json(path)
        if not payload.get("historical_backfill") and payload.get("mode") != "OBJECTIVE_RESEARCH_FEATURES_HISTORICAL_BACKFILL":
            # Current/live research days are still objective facts; accept only if CLOSED and fields are present.
            if payload.get("market_phase") != "CLOSED":
                continue
        rows = {}
        for item in payload.get("features") or []:
            code = str(item.get("code") or "")
            o, c = finite(item.get("open")), finite(item.get("close"))
            if code and o is not None and c is not None and o > 0 and c > 0:
                rows[code] = {"open": o, "close": c, "name": item.get("name")}
        if rows:
            day_rows[d] = rows

    dates = sorted(day_rows)
    if len(dates) < 80:
        raise RuntimeError(f"insufficient daily history: {len(dates)}")

    # Per-code date index to calculate 5d/20d momentum through close D.
    code_dates: dict[str, list[str]] = defaultdict(list)
    for d in dates:
        for code in day_rows[d]:
            code_dates[code].append(d)
    code_pos = {code: {d: i for i, d in enumerate(ds)} for code, ds in code_dates.items()}

    signals: dict[str, dict] = {}
    for d in dates:
        m5, m20, high20 = {}, {}, {}
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
            prior20 = [day_rows[x][code]["close"] for x in ds[max(0, pos - 19): pos + 1]]
            high20[code] = c >= max(prior20) - 1e-12
        common = set(m5) & set(m20)
        if len(common) < 5:
            continue
        r5 = rank_desc({c: m5[c] for c in common})
        r20 = rank_desc({c: m20[c] for c in common})
        n = len(common)
        leaders = [c for c in common if r5[c] <= 3 and r20[c] <= 3 and m5[c] > 0 and m20[c] > 0]
        laggards = [c for c in common if r5[c] >= n - 2 and r20[c] >= n - 2]
        new_high_leaders = [c for c in leaders if high20.get(c)]
        signals[d] = {
            "n": n,
            "leaders": sorted(leaders),
            "laggards": sorted(laggards),
            "new_high_leaders": sorted(new_high_leaders),
            "mom5": m5,
            "mom20": m20,
        }

    date_idx = {d: i for i, d in enumerate(dates)}

    def fwd_return(signal_date: str, code: str, h: int):
        i = date_idx[signal_date]
        if i + 1 >= len(dates) or i + h >= len(dates):
            return None
        entry_d = dates[i + 1]
        exit_d = dates[i + h]
        if code not in day_rows.get(entry_d, {}) or code not in day_rows.get(exit_d, {}):
            return None
        entry = day_rows[entry_d][code]["open"]
        exit_c = day_rows[exit_d][code]["close"]
        return exit_c / entry - 1.0 if entry > 0 else None

    # Precompute same-date universe forward medians for cross-sectional excess returns.
    universe_median: dict[tuple[str, int], float] = {}
    for d, sig in signals.items():
        for h in horizons:
            vals = []
            for code in sig["mom20"]:
                rr = fwd_return(d, code, h)
                if rr is not None:
                    vals.append(rr)
            if vals:
                universe_median[(d, h)] = median(vals)

    leadership = {}
    right_tail = {}
    migration = {}

    for h in horizons:
        lead_events = []
        high_events = []
        migration_days = []
        lead_by_fold = {f["name"]: [] for f in folds}
        high_by_fold = {f["name"]: [] for f in folds}
        mig_by_fold = {f["name"]: [] for f in folds}

        for d, sig in signals.items():
            med_fwd = universe_median.get((d, h))
            if med_fwd is None:
                continue
            for code in sig["leaders"]:
                rr = fwd_return(d, code, h)
                if rr is None:
                    continue
                ex = rr - med_fwd
                lead_events.append(ex)
                for f in folds:
                    if in_fold(d, f):
                        lead_by_fold[f["name"]].append(ex)
            for code in sig["new_high_leaders"]:
                rr = fwd_return(d, code, h)
                if rr is None:
                    continue
                ex = rr - med_fwd
                high_events.append(ex)
                for f in folds:
                    if in_fold(d, f):
                        high_by_fold[f["name"]].append(ex)

            lr = [fwd_return(d, c, h) for c in sig["leaders"]]
            br = [fwd_return(d, c, h) for c in sig["laggards"]]
            lr = [x for x in lr if x is not None]
            br = [x for x in br if x is not None]
            if lr and br:
                spread = mean(lr) - mean(br)
                migration_days.append(spread)
                for f in folds:
                    if in_fold(d, f):
                        mig_by_fold[f["name"]].append(spread)

        lead_sum = summarize(lead_events)
        lead_sum["folds"] = {k: summarize(v) for k, v in lead_by_fold.items()}
        positive_folds = sum((x.get("mean") or 0) > 0 for x in lead_sum["folds"].values() if x.get("n", 0) >= 10)
        lead_sum["positive_folds_with_n10"] = positive_folds
        lead_sum["passes_horizon_gate"] = bool(
            lead_sum["n"] >= 60 and (lead_sum["mean"] or 0) > 0.002 and
            (lead_sum["positive_rate"] or 0) > 0.52 and positive_folds >= 2
        )
        leadership[str(h)] = lead_sum

        high_sum = summarize(high_events)
        high_sum["folds"] = {k: summarize(v) for k, v in high_by_fold.items()}
        high_positive_folds = sum((x.get("mean") or 0) > 0 for x in high_sum["folds"].values() if x.get("n", 0) >= 8)
        high_sum["positive_folds_with_n8"] = high_positive_folds
        high_sum["passes_horizon_gate"] = bool(
            high_sum["n"] >= 40 and (high_sum["mean"] or 0) > 0 and
            (high_sum["positive_rate"] or 0) >= 0.50 and high_positive_folds >= 2
        )
        right_tail[str(h)] = high_sum

        mig_sum = summarize(migration_days)
        mig_sum["folds"] = {k: summarize(v) for k, v in mig_by_fold.items()}
        mig_positive_folds = sum((x.get("mean") or 0) > 0 for x in mig_sum["folds"].values() if x.get("n", 0) >= 10)
        cost_views = {}
        for bps in cfg["cost_scenarios_bps_round_trip"]:
            cost = float(bps) / 10000.0
            cost_views[str(bps)] = {
                "mean_net_spread": r6((mig_sum["mean"] or 0) - cost) if mig_sum["n"] else None,
                "median_net_spread": r6((mig_sum["median"] or 0) - cost) if mig_sum["n"] else None,
            }
        mig_sum["cost_scenarios_bps_round_trip"] = cost_views
        mig_sum["positive_folds_with_n10"] = mig_positive_folds
        net20 = cost_views.get("20", {}).get("mean_net_spread")
        mig_sum["passes_horizon_gate"] = bool(
            mig_sum["n"] >= 50 and net20 is not None and net20 > 0.002 and
            (mig_sum["positive_rate"] or 0) > 0.52 and mig_positive_folds >= 2
        )
        migration[str(h)] = mig_sum

    # Require evidence at more than one horizon; no single horizon can create a research conclusion.
    lead_pass_h = [int(h) for h, x in leadership.items() if x["passes_horizon_gate"]]
    mig_pass_h = [int(h) for h, x in migration.items() if x["passes_horizon_gate"]]
    high_pass_h = [int(h) for h, x in right_tail.items() if x["passes_horizon_gate"]]

    conclusions = {
        "leadership_continuation": {
            "status": "PASS" if len(lead_pass_h) >= 2 else "NO_STABLE_INCREMENT",
            "passing_horizons": lead_pass_h,
            "interpretation": "Strong cross-sectional leaders show persistent forward excess return; use as opportunity-comparison evidence, never as a standalone buy trigger." if len(lead_pass_h) >= 2 else "No sufficiently stable multi-horizon evidence that current leaders alone deserve incremental capital."
        },
        "active_migration_spread": {
            "status": "PASS" if len(mig_pass_h) >= 2 else "NO_STABLE_INCREMENT",
            "passing_horizons": mig_pass_h,
            "interpretation": "Leader-vs-laggard spread remains positive after conservative ETF trading-cost scenarios; supports explicit opportunity-cost comparison when an old holding weakens and an independent new opportunity exists, but does not authorize mechanical rotation." if len(mig_pass_h) >= 2 else "No sufficiently stable net leader-vs-laggard spread to justify systematic capital migration."
        },
        "right_tail_holding": {
            "status": "PASS" if len(high_pass_h) >= 2 else "NO_STABLE_INCREMENT",
            "passing_horizons": high_pass_h,
            "interpretation": "Leaders at 20-day closing highs retain positive forward excess return; supports protecting right-tail winners and rejects new-high-only profit taking." if len(high_pass_h) >= 2 else "No sufficiently stable evidence that leader new highs retain excess return across horizons."
        },
    }
    passing = [k for k, v in conclusions.items() if v["status"] == "PASS"]
    interpretation = "PROMISING_ACTIVE_RETURN_EVIDENCE_REQUIRES_FORMAL_CONVERSION_REVIEW" if passing else "NO_NEW_ACTIVE_RETURN_EVIDENCE"

    result = {
        "research_id": cfg["research_id"],
        "generated_at_beijing": datetime.now(BJ).isoformat(timespec="seconds"),
        "status": "PASS",
        "research_interpretation": interpretation,
        "data_quality": {
            "daily_feature_days": len(dates),
            "first_date": dates[0],
            "last_date": dates[-1],
            "codes": sorted(code_dates),
            "point_in_time": cfg["data"]["point_in_time"],
            "historical_decision_reconstruction": False,
        },
        "method": {
            "leader_definition": "simultaneous top-3 cross-sectional 5d and 20d close momentum, both positive, observed at close D",
            "laggard_definition": "simultaneous bottom-3 cross-sectional 5d and 20d close momentum at close D",
            "entry": "next trading day open after signal close",
            "forward_exit": "close on the h-th trading day after signal date",
            "benchmark": "same-signal-date investable ETF universe median forward return",
            "migration_cost_scenarios_bps_round_trip": cfg["cost_scenarios_bps_round_trip"],
            "no_hidden_score": True,
        },
        "leadership_continuation": leadership,
        "active_migration_spread": migration,
        "right_tail_holding": right_tail,
        "conclusions": conclusions,
        "passing_hypotheses": passing,
        "decision_eligible": False,
        "trade_signal": None,
        "master_override": False,
        "production_integration": False,
        "conversion_review_required": bool(passing),
        "boundary": "Research evidence may inform opportunity comparison, holding right-tail protection, and opportunity-cost review only after formal conversion. It must not create mechanical top-rank buying, bottom-rank selling, rotation, amount, or risk permission."
    }
    dump(OUT, result)
    dump(STATUS, {
        "research_id": cfg["research_id"],
        "generated_at_beijing": result["generated_at_beijing"],
        "status": "PASS",
        "interpretation": interpretation,
        "passing_hypotheses": passing,
        "decision_eligible": False,
        "trade_signal": None,
        "master_override": False,
        "production_integration": False,
        "conversion_review_required": bool(passing),
    })
    print(json.dumps({"interpretation": interpretation, "passing_hypotheses": passing, "data_quality": result["data_quality"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
