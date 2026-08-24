from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def round4(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def load_prices(start: str, end: str) -> pd.DataFrame:
    daily_dir = ROOT / "events/research/daily_features"
    rows = []
    for path in sorted(daily_dir.glob("*.json")):
        d = path.stem
        if d < start or d > end:
            continue
        payload = load_json(path)
        for item in payload.get("features") or []:
            close = item.get("close")
            code = str(item.get("code") or "")
            if code and close is not None:
                rows.append((pd.Timestamp(d), code, float(close)))
    if not rows:
        raise RuntimeError("No historical ETF daily facts found")
    frame = pd.DataFrame(rows, columns=["datetime", "code", "close"])
    return frame.pivot(index="datetime", columns="code", values="close").sort_index()


def union_find_components(corr: pd.DataFrame, threshold: float) -> list[list[str]]:
    cols = list(corr.columns)
    parent = {c: c for c in cols}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            v = corr.loc[a, b]
            if pd.notna(v) and float(v) >= threshold:
                union(a, b)
    groups: dict[str, list[str]] = {}
    for c in cols:
        groups.setdefault(find(c), []).append(c)
    return sorted([sorted(g) for g in groups.values() if len(g) >= 2], key=lambda x: (-len(x), x))


def portfolio_metrics(test: pd.DataFrame, weights: np.ndarray, train_corr: pd.DataFrame, threshold: float) -> dict:
    w = np.asarray(weights, dtype=float)
    w = np.where(np.isfinite(w), w, 0.0)
    if w.sum() <= 0:
        raise ValueError("Non-positive portfolio weights")
    w = w / w.sum()
    r = test.to_numpy(dtype=float) @ w
    ann_vol = np.std(r, ddof=1) * np.sqrt(252) if len(r) > 1 else np.nan
    ann_ret = np.mean(r) * 252
    wealth = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(wealth)
    drawdown = wealth / peak - 1.0
    max_dd = abs(float(np.min(drawdown))) if len(drawdown) else np.nan
    q = np.quantile(r, 0.05) if len(r) else np.nan
    tail = r[r <= q] if len(r) else np.array([])
    cvar95 = abs(float(np.mean(tail))) if len(tail) else np.nan
    asset_vol = test.std(ddof=1).to_numpy(dtype=float) * np.sqrt(252)
    weighted_vol = float(np.dot(w, asset_vol))
    div_ratio = weighted_vol / ann_vol if ann_vol and np.isfinite(ann_vol) and ann_vol > 0 else np.nan
    effective_n = 1.0 / float(np.sum(w * w)) if np.sum(w * w) > 0 else np.nan
    comps = union_find_components(train_corr, threshold)
    component_shares = [float(sum(w[list(test.columns).index(c)] for c in comp if c in test.columns)) for comp in comps]
    high_corr_share = max(component_shares) if component_shares else 0.0
    return {
        "annualized_return": round4(ann_ret),
        "annualized_volatility": round4(ann_vol),
        "max_drawdown": round4(max_dd),
        "cvar_95": round4(cvar95),
        "diversification_ratio": round4(div_ratio),
        "effective_asset_count": round4(effective_n),
        "largest_high_corr_component_weight_share": round4(high_corr_share),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    request = load_json(ROOT / args.request_path)
    cfg = load_json(ROOT / request["config_path"])
    data_cfg = cfg["data"]
    wf = cfg["walk_forward"]
    threshold = float(cfg["correlation"]["high_correlation_threshold"])

    prices = load_prices(data_cfg["start_date"], data_cfg["end_date"])
    returns = prices.pct_change(fill_method=None)
    dates = list(returns.index)
    train_days = int(wf["train_days"])
    test_days = int(wf["test_days"])
    step_days = int(wf["step_days"])
    min_assets = int(data_cfg["minimum_assets_per_fold"])

    from skfolio.optimization import EqualWeighted, InverseVolatility, MaximumDiversification, RiskBudgeting

    model_factories = {
        "EqualWeighted": lambda: EqualWeighted(),
        "InverseVolatility": lambda: InverseVolatility(),
        "RiskBudgeting": lambda: RiskBudgeting(),
        "MaximumDiversification": lambda: MaximumDiversification(),
    }

    folds = []
    failures = []
    start_idx = train_days
    fold_no = 0
    while start_idx + test_days <= len(dates):
        train_dates = dates[start_idx - train_days : start_idx]
        test_dates = dates[start_idx : start_idx + test_days]
        train = returns.loc[train_dates]
        test = returns.loc[test_dates]
        eligible = []
        for c in returns.columns:
            tr = train[c]
            te = test[c]
            if tr.notna().mean() >= 0.95 and te.notna().mean() >= 0.95:
                eligible.append(c)
        if len(eligible) >= min_assets:
            train_x = train[eligible].dropna(axis=0, how="any")
            test_x = test[eligible].dropna(axis=0, how="any")
            if len(train_x) >= int(train_days * 0.85) and len(test_x) >= int(test_days * 0.85):
                fold_no += 1
                corr = train_x.corr()
                comps = union_find_components(corr, threshold)
                pair_rows = []
                for i, a in enumerate(eligible):
                    for b in eligible[i + 1 :]:
                        pair_rows.append((float(corr.loc[a, b]), a, b))
                pair_rows.sort(reverse=True)
                model_results = {}
                for name in cfg["models"]:
                    try:
                        model = model_factories[name]()
                        model.fit(train_x)
                        weights = np.asarray(model.weights_, dtype=float)
                        metrics = portfolio_metrics(test_x, weights, corr, threshold)
                        model_results[name] = {
                            "metrics": metrics,
                            "weights": {c: round4(w) for c, w in zip(eligible, weights)},
                        }
                    except Exception as exc:
                        failures.append({
                            "fold": fold_no,
                            "model": name,
                            "train_end": str(train_dates[-1].date()),
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                folds.append({
                    "fold": fold_no,
                    "train_start": str(train_dates[0].date()),
                    "train_end": str(train_dates[-1].date()),
                    "test_start": str(test_dates[0].date()),
                    "test_end": str(test_dates[-1].date()),
                    "asset_count": len(eligible),
                    "assets": eligible,
                    "high_corr_components": comps,
                    "top_correlated_pairs": [
                        {"a": a, "b": b, "correlation": round4(v)} for v, a, b in pair_rows[:10]
                    ],
                    "models": model_results,
                })
        start_idx += step_days

    baseline = cfg["validation"]["baseline"]
    methods_summary = {}
    promising_methods = []
    for method in cfg["models"]:
        rows = [f for f in folds if method in f["models"] and baseline in f["models"]]
        if not rows:
            continue
        diffs = {k: [] for k in ["annualized_volatility", "max_drawdown", "cvar_95", "diversification_ratio", "effective_asset_count", "largest_high_corr_component_weight_share"]}
        wins = {k: 0 for k in diffs}
        for f in rows:
            m = f["models"][method]["metrics"]
            b = f["models"][baseline]["metrics"]
            for k in diffs:
                mv, bv = m.get(k), b.get(k)
                if mv is None or bv is None:
                    continue
                diffs[k].append(float(mv) - float(bv))
                lower_better = k in {"annualized_volatility", "max_drawdown", "cvar_95", "largest_high_corr_component_weight_share"}
                if (lower_better and mv < bv) or ((not lower_better) and mv > bv):
                    wins[k] += 1
        n = len(rows)
        summary = {
            "fold_count": n,
            "vs_equal_weight": {
                k: {
                    "mean_difference": round4(np.mean(v)) if v else None,
                    "median_difference": round4(np.median(v)) if v else None,
                    "win_rate": round4(wins[k] / len(v)) if v else None,
                }
                for k, v in diffs.items()
            },
        }
        risk_wins = [
            summary["vs_equal_weight"][k]["win_rate"] or 0.0
            for k in ["annualized_volatility", "max_drawdown", "cvar_95", "largest_high_corr_component_weight_share"]
        ]
        concentration_ok = (summary["vs_equal_weight"]["effective_asset_count"]["mean_difference"] or -999) > -4.0
        promising = sum(x >= 0.60 for x in risk_wins) >= 3 and concentration_ok
        summary["stage2_candidate"] = bool(promising and method != baseline)
        if summary["stage2_candidate"]:
            promising_methods.append(method)
        methods_summary[method] = summary

    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "SKFOLIO_STAGE1_WALK_FORWARD_VALIDATION",
        "upstream_commit": cfg["upstream"]["commit"],
        "data_start": data_cfg["start_date"],
        "data_end": data_cfg["end_date"],
        "fold_count": len(folds),
        "failure_count": len(failures),
        "failures": failures,
        "methods_summary": methods_summary,
        "promising_methods": promising_methods,
        "research_interpretation": "PROMISING_FOR_STAGE2" if promising_methods and len(folds) >= int(wf["minimum_folds"]) else "NO_STABLE_INCREMENT_STAGE1",
        "fold_results": folds,
        "decision_eligible": False,
        "trade_signal": None,
        "portfolio_target": None,
        "trial_confirm": None,
        "master_override": False,
        "historical_decision_prohibited": True,
        "interpretation_boundary": "本结果只验证共同风险识别与样本外分散效率。优化权重不得解释为正式目标仓位，不得直接生成风险许可、机会状态、金额或卖出动作。",
    }
    out_path = ROOT / request["output_path"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(out_path, payload)
    status = {
        "schema_version": "1.0",
        "generated_at": payload["generated_at"],
        "mode": "SKFOLIO_RESEARCH_POC",
        "status": "PASS" if folds and not failures else ("DEGRADED" if folds else "FAIL"),
        "request_path": args.request_path,
        "upstream_commit": cfg["upstream"]["commit"],
        "fold_count": len(folds),
        "failure_count": len(failures),
        "research_interpretation": payload["research_interpretation"],
        "decision_eligible": False,
        "trade_signal": None,
        "boundary": payload["interpretation_boundary"],
    }
    status_path = ROOT / request["status_path"]
    status_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(status_path, status)
    print(json.dumps({
        "fold_count": len(folds),
        "failure_count": len(failures),
        "promising_methods": promising_methods,
        "research_interpretation": payload["research_interpretation"],
    }, ensure_ascii=False))
    return 0 if folds and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
