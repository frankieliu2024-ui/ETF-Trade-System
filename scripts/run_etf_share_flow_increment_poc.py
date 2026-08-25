from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_etf_share_flow_stage1 as core

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "research/backtests/etf_share_flow_increment_poc.json"
STATUS = ROOT / "data/state/etf_share_flow_increment_status.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def r4(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(method="average", pct=True)


def add_derived_signals(panel: pd.DataFrame) -> pd.DataFrame:
    parts = []
    required = ["neg_share_change_5d_pct_lag1", "ret_5d_pct", "ret_20d_pct"]
    for _, g in panel.groupby("date", sort=True):
        g = g.copy()
        valid = g[required].dropna().index
        if len(valid) >= 3:
            rs = pct_rank(g.loc[valid, "neg_share_change_5d_pct_lag1"])
            r5 = pct_rank(g.loc[valid, "ret_5d_pct"])
            r20 = pct_rank(g.loc[valid, "ret_20d_pct"])
            X = np.column_stack([np.ones(len(valid)), r5.to_numpy(), r20.to_numpy()])
            y = rs.to_numpy()
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            resid = y - X @ beta
            g.loc[valid, "share_residual_vs_momentum"] = resid
            g.loc[valid, "combo_share_ret5"] = (rs + r5) / 2.0
            g.loc[valid, "combo_share_ret20"] = (rs + r20) / 2.0
            g.loc[valid, "momentum_ensemble"] = (r5 + r20) / 2.0
            g.loc[valid, "combo_share_momentum_ensemble"] = (rs + r5 + r20) / 3.0
            g.loc[valid, "share_rank"] = rs
            g.loc[valid, "ret5_rank"] = r5
            g.loc[valid, "ret20_rank"] = r20
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def daily_corr_summary(df: pd.DataFrame, min_n: int) -> dict:
    rows = []
    for _, g in df[["date", "share_rank", "ret5_rank", "ret20_rank"]].dropna().groupby("date"):
        if len(g) < min_n:
            continue
        rows.append({
            "share_vs_ret5": g["share_rank"].corr(g["ret5_rank"], method="spearman"),
            "share_vs_ret20": g["share_rank"].corr(g["ret20_rank"], method="spearman"),
        })
    if not rows:
        return {"eligible_dates": 0, "mean_share_vs_ret5": None, "mean_share_vs_ret20": None}
    x = pd.DataFrame(rows)
    return {
        "eligible_dates": int(len(x)),
        "mean_share_vs_ret5": r4(x["share_vs_ret5"].mean()),
        "mean_share_vs_ret20": r4(x["share_vs_ret20"].mean()),
        "median_share_vs_ret5": r4(x["share_vs_ret5"].median()),
        "median_share_vs_ret20": r4(x["share_vs_ret20"].median()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request_path")
    args = ap.parse_args()

    req = load_json(ROOT / args.request_path)
    cfg = load_json(ROOT / req["config_path"])
    stage1 = load_json(ROOT / req["stage1_result"])
    if stage1.get("research_interpretation") != "NO_STABLE_INCREMENT_STAGE1":
        raise RuntimeError("Stage1 prerequisite mismatch")
    if stage1.get("decision_eligible") is not False:
        raise RuntimeError("Stage1 boundary mismatch")

    price = core.load_price_panel(req["data_start"], req["data_end"])
    panel, coverage = core.build_panel(price, load_json(core.UNIVERSE))
    panel = add_derived_signals(panel)
    min_n = int(cfg["minimum_cross_section_size"])
    top_k = int(cfg["top_k"])

    fold_results = []
    agg = defaultdict(lambda: defaultdict(list))
    top3agg = defaultdict(lambda: defaultdict(list))
    corr_folds = []
    all_signals = cfg["derived_signals"] + cfg["baseline_signals"]

    for fold in req["folds"]:
        sub = panel[panel["date"].between(pd.Timestamp(fold["test_start"]), pd.Timestamp(fold["test_end"]))]
        corr = daily_corr_summary(sub, min_n)
        corr_folds.append({"fold": fold["name"], **corr})
        for h in cfg["horizons_trading_days"]:
            label = f"fwd_{int(h)}d_pct"
            metrics = {s: core.eval_signal(sub, s, label, min_n, top_k) for s in all_signals}
            fold_results.append({"fold": fold["name"], "horizon_trading_days": int(h), "metrics": metrics})
            for s, m in metrics.items():
                if m["mean_rank_ic"] is not None:
                    agg[s][int(h)].append(float(m["mean_rank_ic"]))
                if m["top3_mean_forward_pct"] is not None:
                    top3agg[s][int(h)].append(float(m["top3_mean_forward_pct"]))

    horizon_summary = {}
    residual_positive_horizons = 0
    combo_increment_horizons = defaultdict(int)
    combo_positive_top3_horizons = defaultdict(int)

    for h in cfg["horizons_trading_days"]:
        h = int(h)
        baseline_means = {b: float(np.mean(agg[b][h])) for b in cfg["baseline_signals"] if agg[b][h]}
        best_baseline_name = max(baseline_means, key=baseline_means.get)
        best_baseline_ic = baseline_means[best_baseline_name]
        best_baseline_top3 = float(np.mean(top3agg[best_baseline_name][h]))
        derived = {}
        for s in cfg["derived_signals"]:
            vals = agg[s][h]
            tvals = top3agg[s][h]
            mic = float(np.mean(vals)) if vals else float("nan")
            mt3 = float(np.mean(tvals)) if tvals else float("nan")
            posfold = sum(v > 0 for v in vals)
            beats = math.isfinite(mic) and mic > best_baseline_ic
            top3_inc = mt3 - best_baseline_top3 if math.isfinite(mt3) else float("nan")
            derived[s] = {
                "fold_count": len(vals),
                "mean_rank_ic": r4(mic),
                "positive_ic_folds": posfold,
                "increment_vs_best_baseline_ic": r4(mic - best_baseline_ic) if math.isfinite(mic) else None,
                "mean_top3_forward_pct": r4(mt3),
                "increment_vs_best_baseline_top3_pct_points": r4(top3_inc),
                "beats_best_baseline_ic": bool(beats),
            }
            if s == "share_residual_vs_momentum" and posfold >= int(cfg["validation"]["required_positive_residual_folds"]):
                residual_positive_horizons += 1
            if s.startswith("combo_") and beats:
                combo_increment_horizons[s] += 1
                if top3_inc > 0:
                    combo_positive_top3_horizons[s] += 1
        horizon_summary[str(h)] = {
            "best_baseline": best_baseline_name,
            "best_baseline_mean_rank_ic": r4(best_baseline_ic),
            "best_baseline_mean_top3_forward_pct": r4(best_baseline_top3),
            "derived": derived,
        }

    qualifying_combos = []
    for s in [x for x in cfg["derived_signals"] if x.startswith("combo_")]:
        ic_ok = combo_increment_horizons[s] >= int(cfg["validation"]["required_combo_increment_horizons"])
        top3_ok = combo_positive_top3_horizons[s] >= int(cfg["validation"]["required_combo_increment_horizons"])
        if ic_ok and (top3_ok or not cfg["validation"]["require_positive_top3_increment_when_ic_beats"]):
            qualifying_combos.append(s)

    residual_ok = residual_positive_horizons >= int(cfg["validation"]["required_residual_horizons"])
    interpretation = "PROMISING_CONDITIONAL_INCREMENT" if residual_ok and qualifying_combos else "NO_ACTIONABLE_CONDITIONAL_INCREMENT"

    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": cfg["mode"],
        "stage1_prerequisite": {
            "research_interpretation": stage1.get("research_interpretation"),
            "passing_signals": stage1.get("passing_signals"),
        },
        "candidate_signal": cfg["candidate_signal"],
        "point_in_time_rule": cfg["point_in_time_rule"],
        "coverage": coverage,
        "cross_section_correlation": corr_folds,
        "fold_results": fold_results,
        "horizon_summary": horizon_summary,
        "validation_summary": {
            "residual_positive_horizons": residual_positive_horizons,
            "combo_increment_horizons": dict(combo_increment_horizons),
            "combo_positive_top3_horizons": dict(combo_positive_top3_horizons),
            "qualifying_combos": qualifying_combos,
        },
        "research_interpretation": interpretation,
        "decision_eligible": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
        "master_override": False,
        "historical_decision_prohibited": True,
        "interpretation_boundary": "Conditional share-flow results remain research evidence only and cannot directly generate risk permission, opportunity status, amount, holding reduction, exit, target weights or a composite capital-efficiency score."
    }
    atomic_json_write(OUT, payload)
    status = {
        "generated_at": payload["generated_at"],
        "status": "PASS",
        "research_interpretation": interpretation,
        "qualifying_combos": qualifying_combos,
        "coverage": coverage,
        "decision_eligible": False,
        "trade_signal": None,
        "master_override": False,
    }
    atomic_json_write(STATUS, status)
    print(json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
