#!/usr/bin/env python3
"""Research-only Qlib-style walk-forward ETF ranking validation.

This runner intentionally does not import QuantMind code or data. It consumes a PIT-safe
flat research matrix produced by the ETF system and compares bounded supervised models
against existing ranking baselines. It never writes production state.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/research/quantmind_qlib_wfa_stage1.json"


def rank_ic(group: pd.DataFrame, score_col: str, label_col: str) -> float | None:
    x = group[score_col]
    y = group[label_col]
    valid = x.notna() & y.notna()
    if valid.sum() < 3:
        return None
    xr = x[valid].rank(method="average")
    yr = y[valid].rank(method="average")
    corr = xr.corr(yr)
    return None if pd.isna(corr) else float(corr)


def cross_section_metrics(df: pd.DataFrame, score_col: str, label_col: str, top_k: int, min_n: int) -> dict:
    ics: list[float] = []
    top_returns: list[float] = []
    bottom_returns: list[float] = []
    eligible_dates = 0
    for _, g in df.groupby("date", sort=True):
        g = g.dropna(subset=[score_col, label_col])
        if len(g) < min_n:
            continue
        eligible_dates += 1
        ic = rank_ic(g, score_col, label_col)
        if ic is not None:
            ics.append(ic)
        ranked = g.sort_values(score_col, ascending=False)
        k = min(top_k, len(ranked))
        top_returns.append(float(ranked.head(k)[label_col].mean()))
        bottom_returns.append(float(ranked.tail(k)[label_col].mean()))
    if not ics:
        return {"eligible_dates": eligible_dates, "mean_rank_ic": None}
    return {
        "eligible_dates": eligible_dates,
        "mean_rank_ic": float(np.mean(ics)),
        "median_rank_ic": float(np.median(ics)),
        "positive_ic_rate": float(np.mean(np.array(ics) > 0)),
        "top_k_mean_forward_pct": float(np.mean(top_returns)),
        "bottom_k_mean_forward_pct": float(np.mean(bottom_returns)),
        "top_bottom_spread_pct_points": float(np.mean(np.array(top_returns) - np.array(bottom_returns))),
    }


def zscore_fit_transform(train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> tuple[np.ndarray, np.ndarray]:
    mu = train[features].mean(axis=0)
    sigma = train[features].std(axis=0).replace(0, 1.0).fillna(1.0)
    x_train = ((train[features] - mu) / sigma).fillna(0.0).to_numpy(float)
    x_test = ((test[features] - mu) / sigma).fillna(0.0).to_numpy(float)
    return x_train, x_test


def ridge_predict(train: pd.DataFrame, test: pd.DataFrame, features: list[str], label: str, alpha: float) -> np.ndarray:
    x_train, x_test = zscore_fit_transform(train, test, features)
    y = train[label].to_numpy(float)
    x1 = np.column_stack([np.ones(len(x_train)), x_train])
    xt1 = np.column_stack([np.ones(len(x_test)), x_test])
    penalty = np.eye(x1.shape[1])
    penalty[0, 0] = 0.0
    beta = np.linalg.pinv(x1.T @ x1 + alpha * penalty) @ x1.T @ y
    return xt1 @ beta


def choose_ridge_alpha(train: pd.DataFrame, features: list[str], label: str, alphas: list[float], min_n: int) -> float:
    dates = sorted(pd.to_datetime(train["date"]).dropna().unique())
    if len(dates) < 60:
        return float(alphas[0])
    split_idx = max(1, int(len(dates) * 0.8))
    inner_train_dates = set(dates[:split_idx])
    inner_valid_dates = set(dates[split_idx:])
    a = train[pd.to_datetime(train["date"]).isin(inner_train_dates)].dropna(subset=[label])
    b = train[pd.to_datetime(train["date"]).isin(inner_valid_dates)].dropna(subset=[label]).copy()
    best_alpha = float(alphas[0])
    best_ic = -math.inf
    for alpha in alphas:
        if a.empty or b.empty:
            continue
        b["_score"] = ridge_predict(a, b, features, label, float(alpha))
        metrics = cross_section_metrics(b, "_score", label, top_k=3, min_n=min_n)
        ic = metrics.get("mean_rank_ic")
        if ic is not None and ic > best_ic:
            best_ic = ic
            best_alpha = float(alpha)
    return best_alpha


def lightgbm_predict(train: pd.DataFrame, test: pd.DataFrame, features: list[str], label: str, params: dict) -> np.ndarray | None:
    try:
        from lightgbm import LGBMRegressor
    except Exception:
        return None
    x_train, x_test = zscore_fit_transform(train, test, features)
    model = LGBMRegressor(
        objective="regression",
        num_leaves=int(params["num_leaves"]),
        max_depth=int(params["max_depth"]),
        learning_rate=float(params["learning_rate"]),
        n_estimators=int(params["n_estimators"]),
        min_child_samples=int(params["min_child_samples"]),
        colsample_bytree=float(params["feature_fraction"]),
        subsample=float(params["bagging_fraction"]),
        subsample_freq=int(params["bagging_freq"]),
        random_state=int(params["random_state"]),
        verbosity=-1,
    )
    model.fit(x_train, train[label].to_numpy(float))
    return model.predict(x_test)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True, help="PIT-safe CSV/Parquet matrix with date/code/features/baselines/forward labels")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--output", required=True)
    args = p.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    dataset_path = Path(args.dataset)
    df = pd.read_parquet(dataset_path) if dataset_path.suffix.lower() in {".parquet", ".pq"} else pd.read_csv(dataset_path)
    df["date"] = pd.to_datetime(df["date"])

    features = cfg["feature_columns"]
    required = {"date", "code", *features, *cfg["baseline_scores"]}
    for h in cfg["horizons_trading_days"]:
        required.add(f"forward_{h}d_pct")
    missing = sorted(required - set(df.columns))
    if missing:
        raise SystemExit(f"missing required columns: {missing}")

    min_n = int(cfg["validation"]["minimum_cross_section_size"])
    top_k = int(cfg["validation"]["top_k"])
    results: list[dict] = []

    for fold in cfg["outer_walk_forward_folds"]:
        train_mask = (df["date"] >= pd.Timestamp(fold["train_start"])) & (df["date"] <= pd.Timestamp(fold["train_end"]))
        test_mask = (df["date"] >= pd.Timestamp(fold["test_start"])) & (df["date"] <= pd.Timestamp(fold["test_end"]))
        for h in cfg["horizons_trading_days"]:
            label = f"forward_{h}d_pct"
            train = df.loc[train_mask].dropna(subset=[label]).copy()
            test = df.loc[test_mask].dropna(subset=[label]).copy()
            row = {"fold": fold["name"], "horizon_trading_days": h, "train_rows": len(train), "test_rows": len(test), "models": {}, "baselines": {}}

            for baseline in cfg["baseline_scores"]:
                row["baselines"][baseline] = cross_section_metrics(test, baseline, label, top_k, min_n)

            ridge_cfg = cfg["models"]["ridge"]
            if ridge_cfg.get("enabled") and not train.empty and not test.empty:
                alpha = choose_ridge_alpha(train, features, label, ridge_cfg["alphas"], min_n)
                test["_ridge_score"] = ridge_predict(train, test, features, label, alpha)
                row["models"]["ridge"] = {"selected_alpha": alpha, **cross_section_metrics(test, "_ridge_score", label, top_k, min_n)}

            lgb_cfg = cfg["models"]["lightgbm"]
            if lgb_cfg.get("enabled") and not train.empty and not test.empty:
                pred = lightgbm_predict(train, test, features, label, lgb_cfg)
                if pred is None:
                    row["models"]["lightgbm"] = {"status": "SKIPPED_OPTIONAL_DEPENDENCY_UNAVAILABLE"}
                else:
                    test["_lgb_score"] = pred
                    row["models"]["lightgbm"] = cross_section_metrics(test, "_lgb_score", label, top_k, min_n)
            results.append(row)

    summary: dict[str, dict] = {}
    for h in cfg["horizons_trading_days"]:
        hrows = [r for r in results if r["horizon_trading_days"] == h]
        baseline_names = cfg["baseline_scores"]
        baseline_means = {
            b: float(np.nanmean([r["baselines"][b].get("mean_rank_ic") for r in hrows]))
            for b in baseline_names
        }
        best_baseline = max(baseline_means, key=baseline_means.get)
        best_ic = baseline_means[best_baseline]
        model_summary = {}
        for model in ["ridge", "lightgbm"]:
            vals = [r["models"].get(model, {}).get("mean_rank_ic") for r in hrows]
            vals = [v for v in vals if v is not None]
            if vals:
                mean_ic = float(np.mean(vals))
                positive_increment_folds = sum(
                    1 for r in hrows
                    if r["models"].get(model, {}).get("mean_rank_ic") is not None
                    and r["models"][model]["mean_rank_ic"] > r["baselines"][best_baseline].get("mean_rank_ic", -math.inf)
                )
                model_summary[model] = {
                    "mean_rank_ic": mean_ic,
                    "increment_vs_best_baseline_rank_ic": mean_ic - best_ic,
                    "positive_increment_folds": positive_increment_folds,
                }
        summary[str(h)] = {"best_baseline": best_baseline, "best_baseline_mean_rank_ic": best_ic, "models": model_summary}

    required_folds = int(cfg["validation"]["required_positive_increment_folds_per_horizon"])
    passes = []
    for model in ["ridge", "lightgbm"]:
        per_h = []
        for h in cfg["horizons_trading_days"]:
            m = summary[str(h)]["models"].get(model)
            per_h.append(bool(m and m["increment_vs_best_baseline_rank_ic"] > 0 and m["positive_increment_folds"] >= required_folds))
        passes.append(all(per_h))

    out = {
        "schema_version": "1.0",
        "mode": cfg["mode"],
        "research_only": True,
        "dataset": str(dataset_path),
        "fold_results": results,
        "horizon_summary": summary,
        "stage1_pass": any(passes),
        "research_interpretation": "PROMISING_REQUIRES_FURTHER_REVIEW" if any(passes) else "NO_STABLE_INCREMENT_STAGE1",
        "decision_eligible": False,
        "production_integration": False,
        "trade_signal": None,
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["horizon_summary"], ensure_ascii=False, indent=2))
    print(f"stage1_pass={out['stage1_pass']}")


if __name__ == "__main__":
    main()
