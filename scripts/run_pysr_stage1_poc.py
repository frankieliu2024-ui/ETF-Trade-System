from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_etf_share_flow_stage1 as share_core

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
UNIVERSE = ROOT / "config/market/etf_monitor_universe.json"


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


def build_panel_with_retry(price: pd.DataFrame, universe: dict) -> tuple[pd.DataFrame, dict]:
    last = None
    for attempt in range(3):
        try:
            return share_core.build_panel(price, universe)
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"official share history fetch failed after retries: {type(last).__name__}: {last}")


def augment_panel(panel: pd.DataFrame, horizons: list[int], min_n: int) -> pd.DataFrame:
    parts = []
    for _, g in panel.groupby("code", sort=True):
        g = g.copy().sort_values("date")
        close = g["close"].astype(float)
        g["ret_1d_pct"] = (close / close.shift(1) - 1.0) * 100.0
        g["ret_10d_pct"] = (close / close.shift(10) - 1.0) * 100.0
        g["vol_20d_pct"] = g["ret_1d_pct"].rolling(20, min_periods=20).std()
        for h in horizons:
            g[f"label_date_{h}d"] = g["date"].shift(-h)
        parts.append(g)
    x = pd.concat(parts, ignore_index=True)

    ranked = []
    raw_to_rank = {
        "neg_share_change_1d_pct_lag1": "rank_neg_share_change_1d",
        "neg_share_change_3d_pct_lag1": "rank_neg_share_change_3d",
        "neg_share_change_5d_pct_lag1": "rank_neg_share_change_5d",
        "ret_1d_pct": "rank_ret_1d",
        "ret_5d_pct": "rank_ret_5d",
        "ret_10d_pct": "rank_ret_10d",
        "ret_20d_pct": "rank_ret_20d",
        "vol_20d_pct": "rank_vol_20d",
    }
    for _, g in x.groupby("date", sort=True):
        g = g.copy()
        for raw, rank_name in raw_to_rank.items():
            valid = g[raw].dropna()
            if len(valid) >= min_n and valid.nunique() >= 2:
                g.loc[valid.index, rank_name] = pct_rank(valid)
        if all(c in g for c in ["rank_ret_5d", "rank_ret_20d"]):
            g["momentum_ensemble"] = (g["rank_ret_5d"] + g["rank_ret_20d"]) / 2.0
        if all(c in g for c in ["rank_neg_share_change_5d", "rank_ret_5d", "rank_ret_20d"]):
            g["share_momentum_ensemble"] = (
                g["rank_neg_share_change_5d"] + g["rank_ret_5d"] + g["rank_ret_20d"]
            ) / 3.0
        for h in horizons:
            label = f"fwd_{h}d_pct"
            target = f"target_rank_{h}d"
            valid = g[label].dropna()
            if len(valid) >= min_n and valid.nunique() >= 2:
                g.loc[valid.index, target] = pct_rank(valid)
        ranked.append(g)
    return pd.concat(ranked, ignore_index=True).sort_values(["date", "code"])


def eval_prediction(df: pd.DataFrame, pred: np.ndarray, label: str, min_n: int, top_k: int) -> dict:
    z = df[["date", "code", label]].copy()
    z["prediction"] = np.asarray(pred, dtype=float)
    ics, top1, topk, bottom = [], [], [], []
    for _, g in z.dropna().groupby("date"):
        if len(g) < min_n or g["prediction"].nunique() < 2 or g[label].nunique() < 2:
            continue
        ic = g["prediction"].corr(g[label], method="spearman")
        if pd.isna(ic):
            continue
        ics.append(float(ic))
        o = g.sort_values("prediction", ascending=False)
        top1.append(float(o.iloc[0][label]))
        topk.append(float(o.head(min(top_k, len(o)))[label].mean()))
        bottom.append(float(o.iloc[-1][label]))
    return {
        "eligible_dates": len(ics),
        "mean_rank_ic": r4(np.mean(ics)) if ics else None,
        "median_rank_ic": r4(np.median(ics)) if ics else None,
        "positive_ic_rate": r4(np.mean(np.asarray(ics) > 0)) if ics else None,
        "top1_mean_forward_pct": r4(np.mean(top1)) if top1 else None,
        "top3_mean_forward_pct": r4(np.mean(topk)) if topk else None,
        "top1_minus_bottom_spread_pct_points": r4(np.mean(np.asarray(top1) - np.asarray(bottom))) if top1 else None,
    }


def fit_one(train: pd.DataFrame, test: pd.DataFrame, features: list[str], target: str, pysr_cfg: dict, seed: int):
    from pysr import PySRRegressor

    model = PySRRegressor(
        niterations=int(pysr_cfg["niterations"]),
        populations=int(pysr_cfg["populations"]),
        population_size=int(pysr_cfg["population_size"]),
        maxsize=int(pysr_cfg["maxsize"]),
        maxdepth=int(pysr_cfg["maxdepth"]),
        binary_operators=list(pysr_cfg["binary_operators"]),
        unary_operators=list(pysr_cfg["unary_operators"]),
        model_selection=str(pysr_cfg["model_selection"]),
        parallelism=str(pysr_cfg["parallelism"]),
        random_state=int(seed),
        deterministic=True,
        verbosity=0,
        progress=False,
        timeout_in_seconds=90,
    )
    Xtr = train[features].astype(float)
    ytr = train[target].astype(float)
    Xte = test[features].astype(float)
    model.fit(Xtr, ytr, variable_names=features)
    pred = model.predict(Xte)
    try:
        best = model.get_best()
        best_meta = {
            "equation": str(best.get("equation")) if hasattr(best, "get") else str(model.sympy()),
            "complexity": int(best.get("complexity")) if hasattr(best, "get") and best.get("complexity") is not None else None,
            "loss": r4(best.get("loss")) if hasattr(best, "get") else None,
            "score": r4(best.get("score")) if hasattr(best, "get") else None,
        }
    except Exception:
        best_meta = {"equation": str(model.sympy()), "complexity": None, "loss": None, "score": None}
    return np.asarray(pred, dtype=float), best_meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request_path")
    args = ap.parse_args()
    req = load_json(ROOT / args.request_path)
    cfg = load_json(ROOT / req["config_path"])
    out_path = ROOT / req["result_path"]
    status_path = ROOT / req["status_path"]

    price = share_core.load_price_panel(req["data_start"], req["data_end"])
    panel, coverage = build_panel_with_retry(price, load_json(UNIVERSE))
    horizons = [int(x) for x in cfg["horizons_trading_days"]]
    min_n = int(cfg["minimum_cross_section_size"])
    top_k = int(cfg["top_k"])
    panel = augment_panel(panel, horizons, min_n)
    features = list(cfg["feature_columns"])
    baseline_signals = list(cfg["baseline_signals"])

    fold_results = []
    agg_ic = defaultdict(lambda: defaultdict(list))
    agg_top3 = defaultdict(lambda: defaultdict(list))
    failures = []

    for fold_idx, fold in enumerate(req["folds"]):
        train_start = pd.Timestamp(fold["train_start"])
        train_end = pd.Timestamp(fold["train_end"])
        test_start = pd.Timestamp(fold["test_start"])
        test_end = pd.Timestamp(fold["test_end"])
        data_end = pd.Timestamp(req["data_end"])
        for h in horizons:
            label = f"fwd_{h}d_pct"
            target = f"target_rank_{h}d"
            label_date = f"label_date_{h}d"
            required = features + [target, label, label_date]
            train = panel[
                panel["date"].between(train_start, train_end)
                & (panel[label_date] <= train_end)
            ].dropna(subset=required).copy()
            test = panel[
                panel["date"].between(test_start, test_end)
                & (panel[label_date] <= data_end)
            ].dropna(subset=features + [label, label_date]).copy()
            if train.empty or test.empty:
                failures.append({"fold": fold["name"], "horizon": h, "error": "empty train/test after PIT purge"})
                continue
            seed = int(cfg["pysr"]["random_state"]) + fold_idx * 100 + h
            try:
                pred, formula = fit_one(train, test, features, target, cfg["pysr"], seed)
                pysr_metrics = eval_prediction(test, pred, label, min_n, top_k)
                baselines = {b: share_core.eval_signal(test, b, label, min_n, top_k) for b in baseline_signals}
                fold_results.append({
                    "fold": fold["name"],
                    "horizon_trading_days": h,
                    "train_rows": int(len(train)),
                    "test_rows": int(len(test)),
                    "formula": formula,
                    "pysr": pysr_metrics,
                    "baselines": baselines,
                })
                if pysr_metrics["mean_rank_ic"] is not None:
                    agg_ic["pysr"][h].append(float(pysr_metrics["mean_rank_ic"]))
                if pysr_metrics["top3_mean_forward_pct"] is not None:
                    agg_top3["pysr"][h].append(float(pysr_metrics["top3_mean_forward_pct"]))
                for b, m in baselines.items():
                    if m["mean_rank_ic"] is not None:
                        agg_ic[b][h].append(float(m["mean_rank_ic"]))
                    if m["top3_mean_forward_pct"] is not None:
                        agg_top3[b][h].append(float(m["top3_mean_forward_pct"]))
            except Exception as exc:
                failures.append({"fold": fold["name"], "horizon": h, "error": f"{type(exc).__name__}: {exc}"})

    horizon_summary = {}
    all_horizons_pass = True
    for h in horizons:
        p_ic = agg_ic["pysr"][h]
        p_top3 = agg_top3["pysr"][h]
        baseline_ic_means = {
            b: float(np.mean(agg_ic[b][h])) for b in baseline_signals if agg_ic[b][h]
        }
        baseline_top3_means = {
            b: float(np.mean(agg_top3[b][h])) for b in baseline_signals if agg_top3[b][h]
        }
        if not p_ic or not baseline_ic_means or not baseline_top3_means:
            horizon_summary[str(h)] = {"stage1_pass": False, "reason": "insufficient successful fold metrics"}
            all_horizons_pass = False
            continue
        mean_ic = float(np.mean(p_ic))
        mean_top3 = float(np.mean(p_top3)) if p_top3 else float("nan")
        best_ic_name = max(baseline_ic_means, key=baseline_ic_means.get)
        best_top3_name = max(baseline_top3_means, key=baseline_top3_means.get)
        best_ic = baseline_ic_means[best_ic_name]
        best_top3 = baseline_top3_means[best_top3_name]
        positive_folds = sum(x > 0 for x in p_ic)
        pass_h = (
            len(p_ic) == len(req["folds"])
            and positive_folds >= int(cfg["validation"]["required_positive_ic_folds_per_horizon"])
            and mean_ic > best_ic
            and math.isfinite(mean_top3)
            and mean_top3 > best_top3
        )
        all_horizons_pass = all_horizons_pass and pass_h
        horizon_summary[str(h)] = {
            "fold_count": len(p_ic),
            "mean_rank_ic": r4(mean_ic),
            "positive_ic_folds": positive_folds,
            "mean_top3_forward_pct": r4(mean_top3),
            "best_baseline_rank_ic_name": best_ic_name,
            "best_baseline_mean_rank_ic": r4(best_ic),
            "increment_vs_best_baseline_rank_ic": r4(mean_ic - best_ic),
            "best_baseline_top3_name": best_top3_name,
            "best_baseline_mean_top3_forward_pct": r4(best_top3),
            "increment_vs_best_baseline_top3_pct_points": r4(mean_top3 - best_top3),
            "stage1_pass": bool(pass_h),
        }

    complete = len(fold_results) == len(req["folds"]) * len(horizons) and not failures
    interpretation = "PROMISING_FOR_STAGE2" if complete and all_horizons_pass else "NO_STABLE_INCREMENT_STAGE1"
    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": cfg["mode"],
        "upstream": cfg["upstream"],
        "objective": cfg["objective"],
        "point_in_time_rule": cfg["point_in_time_rule"],
        "complexity_boundary": cfg["complexity_boundary"],
        "features": features,
        "coverage": coverage,
        "fold_horizon_runs": len(fold_results),
        "failure_count": len(failures),
        "failures": failures,
        "fold_results": fold_results,
        "horizon_summary": horizon_summary,
        "research_interpretation": interpretation,
        "decision_eligible": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
        "master_override": False,
        "historical_decision_prohibited": True,
        "interpretation_boundary": "PySR formula output is research evidence only. It cannot directly create risk permission, opportunity status, Trial/Confirm, amount, holding reduction, exit, target weights or a capital-efficiency score."
    }
    atomic_json_write(out_path, payload)
    status = {
        "generated_at": payload["generated_at"],
        "status": "PASS" if complete else "FAILED",
        "research_interpretation": interpretation,
        "fold_horizon_runs": len(fold_results),
        "failure_count": len(failures),
        "horizon_summary": horizon_summary,
        "decision_eligible": False,
        "trade_signal": None,
        "master_override": False,
    }
    atomic_json_write(status_path, status)
    print(json.dumps(status, ensure_ascii=False))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
