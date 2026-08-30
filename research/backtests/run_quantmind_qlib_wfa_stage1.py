#!/usr/bin/env python3
"""Research-only Qlib-style walk-forward ETF ranking validation.

No QuantMind code/data is imported. The runner consumes a PIT-safe ETF matrix,
compares bounded supervised models with existing baselines, purges unrealized
training labels, and evaluates turnover-adjusted top-k returns. It never writes
production state.
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
    valid = group[score_col].notna() & group[label_col].notna()
    if valid.sum() < 3:
        return None
    corr = group.loc[valid, score_col].rank(method="average").corr(
        group.loc[valid, label_col].rank(method="average")
    )
    return None if pd.isna(corr) else float(corr)


def cross_section_metrics(
    df: pd.DataFrame,
    score_col: str,
    label_col: str,
    top_k: int,
    min_n: int,
    round_trip_cost_bps: float,
) -> dict:
    ics: list[float] = []
    top_returns: list[float] = []
    bottom_returns: list[float] = []
    net_top_returns: list[float] = []
    turnovers: list[float] = []
    prev_top: set[str] | None = None
    eligible_dates = 0
    cost_pct_points = float(round_trip_cost_bps) / 100.0

    for _, g in df.groupby("date", sort=True):
        g = g.dropna(subset=[score_col, label_col]).copy()
        if len(g) < min_n or g[score_col].nunique() < 2 or g[label_col].nunique() < 2:
            continue
        ic = rank_ic(g, score_col, label_col)
        if ic is None:
            continue
        eligible_dates += 1
        ics.append(ic)
        ranked = g.sort_values(score_col, ascending=False)
        k = min(top_k, len(ranked))
        top = ranked.head(k)
        bottom = ranked.tail(k)
        top_ret = float(top[label_col].mean())
        bottom_ret = float(bottom[label_col].mean())
        current_top = set(top["code"].astype(str))
        if prev_top is None:
            turnover = 1.0
        else:
            denom = max(1, k)
            turnover = 1.0 - len(current_top & prev_top) / denom
        net_top = top_ret - turnover * cost_pct_points
        top_returns.append(top_ret)
        bottom_returns.append(bottom_ret)
        turnovers.append(turnover)
        net_top_returns.append(net_top)
        prev_top = current_top

    if not ics:
        return {"eligible_dates": eligible_dates, "mean_rank_ic": None}
    return {
        "eligible_dates": eligible_dates,
        "mean_rank_ic": float(np.mean(ics)),
        "median_rank_ic": float(np.median(ics)),
        "positive_ic_rate": float(np.mean(np.asarray(ics) > 0)),
        "top_k_mean_forward_pct": float(np.mean(top_returns)),
        "bottom_k_mean_forward_pct": float(np.mean(bottom_returns)),
        "top_bottom_spread_pct_points": float(np.mean(np.asarray(top_returns) - np.asarray(bottom_returns))),
        "mean_top_k_turnover": float(np.mean(turnovers)),
        "turnover_adjusted_top_k_mean_forward_pct": float(np.mean(net_top_returns)),
        "assumed_round_trip_cost_bps": float(round_trip_cost_bps),
    }


def zscore_fit_transform(train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> tuple[np.ndarray, np.ndarray]:
    mu = train[features].mean(axis=0)
    sigma = train[features].std(axis=0).replace(0, 1.0).fillna(1.0)
    return (
        ((train[features] - mu) / sigma).fillna(0.0).to_numpy(float),
        ((test[features] - mu) / sigma).fillna(0.0).to_numpy(float),
    )


def ridge_predict(train: pd.DataFrame, test: pd.DataFrame, features: list[str], label: str, alpha: float) -> np.ndarray:
    x_train, x_test = zscore_fit_transform(train, test, features)
    y = train[label].to_numpy(float)
    x1 = np.column_stack([np.ones(len(x_train)), x_train])
    xt1 = np.column_stack([np.ones(len(x_test)), x_test])
    penalty = np.eye(x1.shape[1]); penalty[0, 0] = 0.0
    beta = np.linalg.pinv(x1.T @ x1 + alpha * penalty) @ x1.T @ y
    return xt1 @ beta


def choose_ridge_alpha(
    train: pd.DataFrame,
    features: list[str],
    label: str,
    alphas: list[float],
    min_n: int,
    top_k: int,
    cost_bps: float,
) -> float:
    dates = sorted(pd.to_datetime(train["date"]).dropna().unique())
    if len(dates) < 60:
        return float(alphas[0])
    split_idx = max(1, int(len(dates) * 0.8))
    inner_train_dates = set(dates[:split_idx]); inner_valid_dates = set(dates[split_idx:])
    a = train[pd.to_datetime(train["date"]).isin(inner_train_dates)].dropna(subset=[label])
    b = train[pd.to_datetime(train["date"]).isin(inner_valid_dates)].dropna(subset=[label]).copy()
    best_alpha, best_ic = float(alphas[0]), -math.inf
    for alpha in alphas:
        if a.empty or b.empty:
            continue
        b["_score"] = ridge_predict(a, b, features, label, float(alpha))
        m = cross_section_metrics(b, "_score", label, top_k, min_n, cost_bps)
        ic = m.get("mean_rank_ic")
        if ic is not None and ic > best_ic:
            best_ic, best_alpha = ic, float(alpha)
    return best_alpha


def lightgbm_predict(train: pd.DataFrame, test: pd.DataFrame, features: list[str], label: str, params: dict) -> np.ndarray | None:
    try:
        from lightgbm import LGBMRegressor
    except Exception:
        return None
    x_train, x_test = zscore_fit_transform(train, test, features)
    model = LGBMRegressor(
        objective="regression",
        num_leaves=int(params["num_leaves"]), max_depth=int(params["max_depth"]),
        learning_rate=float(params["learning_rate"]), n_estimators=int(params["n_estimators"]),
        min_child_samples=int(params["min_child_samples"]), colsample_bytree=float(params["feature_fraction"]),
        subsample=float(params["bagging_fraction"]), subsample_freq=int(params["bagging_freq"]),
        random_state=int(params["random_state"]), verbosity=-1,
    )
    model.fit(x_train, train[label].to_numpy(float))
    return model.predict(x_test)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--output", required=True)
    args = p.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    dataset_path = Path(args.dataset)
    df = pd.read_parquet(dataset_path) if dataset_path.suffix.lower() in {".parquet", ".pq"} else pd.read_csv(dataset_path)
    df["date"] = pd.to_datetime(df["date"])
    for h in cfg["horizons_trading_days"]:
        df[f"label_date_{h}d"] = pd.to_datetime(df[f"label_date_{h}d"])

    features = list(cfg["feature_columns"])
    required = {"date", "code", *features, *cfg["baseline_scores"]}
    for h in cfg["horizons_trading_days"]:
        required.update({f"forward_{h}d_pct", f"label_date_{h}d"})
    missing = sorted(required - set(df.columns))
    if missing:
        raise SystemExit(f"missing required columns: {missing}")

    min_n = int(cfg["validation"]["minimum_cross_section_size"])
    top_k = int(cfg["validation"]["top_k"])
    cost_bps = float(cfg["validation"]["transaction_cost_bps_round_trip"])
    results: list[dict] = []

    for fold in cfg["outer_walk_forward_folds"]:
        train_start, train_end = pd.Timestamp(fold["train_start"]), pd.Timestamp(fold["train_end"])
        test_start, test_end = pd.Timestamp(fold["test_start"]), pd.Timestamp(fold["test_end"])
        for h in cfg["horizons_trading_days"]:
            label, label_date = f"forward_{h}d_pct", f"label_date_{h}d"
            train = df[(df["date"] >= train_start) & (df["date"] <= train_end) & (df[label_date] <= train_end)].dropna(subset=features + [label]).copy()
            test = df[(df["date"] >= test_start) & (df["date"] <= test_end) & (df[label_date] <= pd.Timestamp(cfg["data_end"]))].dropna(subset=features + [label]).copy()
            row = {"fold": fold["name"], "horizon_trading_days": h, "train_rows": len(train), "test_rows": len(test), "models": {}, "baselines": {}}

            for baseline in cfg["baseline_scores"]:
                row["baselines"][baseline] = cross_section_metrics(test, baseline, label, top_k, min_n, cost_bps)

            ridge_cfg = cfg["models"]["ridge"]
            if ridge_cfg.get("enabled") and not train.empty and not test.empty:
                alpha = choose_ridge_alpha(train, features, label, ridge_cfg["alphas"], min_n, top_k, cost_bps)
                test["_ridge_score"] = ridge_predict(train, test, features, label, alpha)
                row["models"]["ridge"] = {"selected_alpha": alpha, **cross_section_metrics(test, "_ridge_score", label, top_k, min_n, cost_bps)}

            lgb_cfg = cfg["models"]["lightgbm"]
            if lgb_cfg.get("enabled") and not train.empty and not test.empty:
                pred = lightgbm_predict(train, test, features, label, lgb_cfg)
                if pred is None:
                    row["models"]["lightgbm"] = {"status": "SKIPPED_OPTIONAL_DEPENDENCY_UNAVAILABLE"}
                else:
                    test["_lgb_score"] = pred
                    row["models"]["lightgbm"] = cross_section_metrics(test, "_lgb_score", label, top_k, min_n, cost_bps)
            results.append(row)

    summary: dict[str, dict] = {}
    for h in cfg["horizons_trading_days"]:
        hrows = [r for r in results if r["horizon_trading_days"] == h]
        baseline_means = {b: float(np.nanmean([r["baselines"][b].get("mean_rank_ic") for r in hrows])) for b in cfg["baseline_scores"]}
        best_baseline = max(baseline_means, key=baseline_means.get); best_ic = baseline_means[best_baseline]
        best_baseline_net = float(np.nanmean([r["baselines"][best_baseline].get("turnover_adjusted_top_k_mean_forward_pct") for r in hrows]))
        model_summary = {}
        for model in ["ridge", "lightgbm"]:
            model_rows = [r for r in hrows if r["models"].get(model, {}).get("mean_rank_ic") is not None]
            if not model_rows:
                continue
            vals = [r["models"][model]["mean_rank_ic"] for r in model_rows]
            net_vals = [r["models"][model]["turnover_adjusted_top_k_mean_forward_pct"] for r in model_rows]
            mean_ic, mean_net = float(np.mean(vals)), float(np.mean(net_vals))
            positive_increment_folds = sum(r["models"][model]["mean_rank_ic"] > r["baselines"][best_baseline].get("mean_rank_ic", -math.inf) for r in model_rows)
            positive_net_increment_folds = sum(r["models"][model]["turnover_adjusted_top_k_mean_forward_pct"] > r["baselines"][best_baseline].get("turnover_adjusted_top_k_mean_forward_pct", math.inf) for r in model_rows)
            model_summary[model] = {
                "fold_count": len(model_rows), "mean_rank_ic": mean_ic,
                "increment_vs_best_baseline_rank_ic": mean_ic - best_ic,
                "positive_increment_folds": positive_increment_folds,
                "turnover_adjusted_top_k_mean_forward_pct": mean_net,
                "net_return_increment_vs_best_baseline_pct_points": mean_net - best_baseline_net,
                "positive_net_increment_folds": positive_net_increment_folds,
            }
        summary[str(h)] = {
            "best_baseline": best_baseline,
            "best_baseline_mean_rank_ic": best_ic,
            "best_baseline_turnover_adjusted_top_k_mean_forward_pct": best_baseline_net,
            "models": model_summary,
        }

    required_folds = int(cfg["validation"]["required_positive_increment_folds_per_horizon"])
    model_pass = {}
    for model in ["ridge", "lightgbm"]:
        per_h = []
        for h in cfg["horizons_trading_days"]:
            m = summary[str(h)]["models"].get(model)
            per_h.append(bool(
                m and m["fold_count"] == len(cfg["outer_walk_forward_folds"])
                and m["increment_vs_best_baseline_rank_ic"] > 0
                and m["positive_increment_folds"] >= required_folds
                and m["net_return_increment_vs_best_baseline_pct_points"] > 0
                and m["positive_net_increment_folds"] >= required_folds
            ))
        model_pass[model] = all(per_h)

    passed = any(model_pass.values())
    out = {
        "schema_version": "1.1", "mode": cfg["mode"], "research_only": True,
        "dataset": str(dataset_path), "fold_results": results, "horizon_summary": summary,
        "model_pass": model_pass, "stage1_pass": passed,
        "research_interpretation": "PROMISING_REQUIRES_FURTHER_REVIEW" if passed else "NO_STABLE_INCREMENT_STAGE1",
        "decision_eligible": False, "production_integration": False, "trade_signal": None,
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["horizon_summary"], ensure_ascii=False, indent=2)); print(f"stage1_pass={passed}")


if __name__ == "__main__":
    main()
