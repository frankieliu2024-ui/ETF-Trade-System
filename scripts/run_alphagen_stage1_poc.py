from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY_DIR = ROOT / "events/research/daily_features"
OUT_PATH = ROOT / "research/backtests/alphagen_stage1_validation.json"
STATUS_PATH = ROOT / "data/state/alphagen_research_status.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def r4(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def load_panel(start: str, end: str) -> pd.DataFrame:
    rows = []
    for path in sorted(DAILY_DIR.glob("*.json")):
        d = path.stem
        if d < start or d > end:
            continue
        p = load_json(path)
        for x in p.get("features") or []:
            volume = x.get("volume")
            amount = x.get("amount")
            vwap = None
            try:
                if float(volume) > 0:
                    vwap = float(amount) / float(volume)
            except (TypeError, ValueError):
                pass
            rows.append({
                "date": pd.Timestamp(d), "code": str(x.get("code") or ""),
                "open": x.get("open"), "close": x.get("close"), "high": x.get("high"),
                "low": x.get("low"), "volume": volume, "vwap": vwap,
            })
    f = pd.DataFrame(rows)
    if f.empty:
        raise RuntimeError("No historical ETF daily features")
    for c in ["open", "close", "high", "low", "volume", "vwap"]:
        f[c] = pd.to_numeric(f[c], errors="coerce")
    return f.dropna(subset=["close"]).sort_values(["date", "code"]).reset_index(drop=True)


def import_alphagen(upstream_root: Path):
    sys.path.insert(0, str(upstream_root))
    import torch
    import alphagen_qlib.stock_data as sd
    sd._QLIB_INITIALIZED = True
    from alphagen_qlib.stock_data import StockData, FeatureType
    from alphagen_qlib.calculator import QLibStockDataCalculator
    from alphagen.data.expression import Feature, Ref
    from alphagen.models.linear_alpha_pool import MseAlphaPool
    from alphagen.rl.env.wrapper import AlphaEnv
    from alphagen.rl.policy import LSTMSharedNet
    from sb3_contrib import MaskablePPO
    return torch, StockData, FeatureType, QLibStockDataCalculator, Feature, Ref, MseAlphaPool, AlphaEnv, LSTMSharedNet, MaskablePPO


def make_stock_data(frame, start, end, horizon, backtrack, train_mode, api):
    torch, StockData, FeatureType, *_ = api
    dates = sorted(frame["date"].drop_duplicates().tolist())
    codes = sorted(frame["code"].drop_duplicates().tolist())
    date_to_i = {d: i for i, d in enumerate(dates)}
    s_candidates = [d for d in dates if d >= pd.Timestamp(start)]
    e_candidates = [d for d in dates if d <= pd.Timestamp(end)]
    if not s_candidates or not e_candidates:
        raise RuntimeError(f"Segment unavailable {start}..{end}")
    s = date_to_i[s_candidates[0]]
    declared_end = date_to_i[e_candidates[-1]]
    core_end = declared_end - horizon if train_mode else declared_end
    if core_end <= s:
        raise RuntimeError("Segment too short after PIT label purge")
    if s < backtrack:
        raise RuntimeError(f"Need {backtrack} pre-segment trading days before {start}")
    if core_end + horizon >= len(dates):
        raise RuntimeError(f"Need {horizon} future outcome days after {end}")
    seg_dates = dates[s - backtrack: core_end + horizon + 1]
    core_dates = dates[s: core_end + 1]
    features = ["open", "close", "high", "low", "volume", "vwap"]
    mats = []
    for col in features:
        pv = frame.pivot(index="date", columns="code", values=col).reindex(index=seg_dates, columns=codes)
        mats.append(pv.to_numpy(dtype=np.float32))
    arr = np.stack(mats, axis=1)
    tensor = torch.tensor(arr, dtype=torch.float32, device=torch.device("cpu"))
    data = StockData(
        instrument=codes,
        start_time=str(pd.Timestamp(core_dates[0]).date()),
        end_time=str(pd.Timestamp(core_dates[-1]).date()),
        max_backtrack_days=backtrack,
        max_future_days=horizon,
        features=list(FeatureType),
        device=torch.device("cpu"),
        preloaded_data=(tensor, pd.Index(seg_dates), pd.Index(codes)),
    )
    return data


def raw_score_tables(frame: pd.DataFrame, dates, codes, horizon):
    close = frame.pivot(index="date", columns="code", values="close").sort_index().reindex(columns=codes)
    future = (close.shift(-horizon) / close - 1.0) * 100.0
    ret5 = (close / close.shift(5) - 1.0) * 100.0
    ret20 = (close / close.shift(20) - 1.0) * 100.0
    idx = pd.Index(dates)
    return future.reindex(idx), ret5.reindex(idx), ret20.reindex(idx)


def eval_matrix(score: pd.DataFrame, actual: pd.DataFrame, min_n: int, top_k: int) -> dict:
    ics, top1, topk, bottom = [], [], [], []
    eligible = 0
    for d in score.index.intersection(actual.index):
        g = pd.DataFrame({"s": score.loc[d], "a": actual.loc[d]}).replace([np.inf, -np.inf], np.nan).dropna()
        if len(g) < min_n or g["s"].nunique() < 2 or g["a"].nunique() < 2:
            continue
        ic = g["s"].corr(g["a"], method="spearman")
        if pd.isna(ic):
            continue
        eligible += 1
        ics.append(float(ic))
        ordered = g.sort_values("s", ascending=False)
        top1.append(float(ordered.iloc[0]["a"]))
        topk.append(float(ordered.head(min(top_k, len(ordered)))["a"].mean()))
        bottom.append(float(ordered.iloc[-1]["a"]))
    return {
        "eligible_dates": eligible,
        "mean_rank_ic": r4(np.mean(ics)) if ics else None,
        "median_rank_ic": r4(np.median(ics)) if ics else None,
        "positive_ic_rate": r4(np.mean(np.asarray(ics) > 0)) if ics else None,
        "top1_mean_forward_pct": r4(np.mean(top1)) if top1 else None,
        "top1_positive_rate": r4(np.mean(np.asarray(top1) > 0)) if top1 else None,
        "top3_mean_forward_pct": r4(np.mean(topk)) if topk else None,
        "bottom_mean_forward_pct": r4(np.mean(bottom)) if bottom else None,
        "top1_minus_bottom_spread_pct_points": r4(np.mean(np.asarray(top1) - np.asarray(bottom))) if top1 else None,
    }


def run_one(frame, fold, horizon, cfg, api):
    torch, _, FeatureType, Calculator, Feature, Ref, MseAlphaPool, AlphaEnv, LSTMSharedNet, MaskablePPO = api
    mine = cfg["alpha_mining"]
    seed = int(mine["seed"]) + horizon + sum(ord(c) for c in fold["name"])
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    back = int(mine["max_backtrack_days"])
    train_data = make_stock_data(frame, fold["train_start"], fold["train_end"], horizon, back, True, api)
    test_data = make_stock_data(frame, fold["test_start"], fold["test_end"], horizon, back, False, api)
    close = Feature(FeatureType.CLOSE)
    target = Ref(close, -horizon) / close - 1.0
    train_calc = Calculator(train_data, target)
    test_calc = Calculator(test_data, target)
    pool = MseAlphaPool(
        capacity=int(mine["pool_capacity"]), calculator=train_calc,
        ic_lower_bound=None, l1_alpha=float(mine["l1_alpha"]), device=torch.device("cpu"),
    )
    env = AlphaEnv(pool=pool, device=torch.device("cpu"), print_expr=False)
    model = MaskablePPO(
        "MlpPolicy", env,
        policy_kwargs=dict(features_extractor_class=LSTMSharedNet,
                           features_extractor_kwargs=dict(n_layers=2, d_model=64, dropout=0.1, device=torch.device("cpu"))),
        gamma=1.0, ent_coef=0.01, batch_size=64, n_steps=256,
        device=torch.device("cpu"), verbose=0, seed=seed,
    )
    model.learn(total_timesteps=int(mine["total_timesteps"]))
    if pool.size == 0:
        raise RuntimeError("AlphaGen produced empty alpha pool")
    exprs = pool.exprs[:pool.size]
    weights = pool.weights
    score_tensor = test_calc.make_ensemble_alpha(exprs, weights)
    core_dates = list(test_data._dates[back:back + test_data.n_days])
    codes = list(test_data.stock_ids)
    score = pd.DataFrame(score_tensor.detach().cpu().numpy(), index=core_dates, columns=codes)
    actual, mom5, mom20 = raw_score_tables(frame, core_dates, codes, horizon)
    min_n = int(cfg["validation"]["minimum_cross_section_size"])
    top_k = int(cfg["validation"]["top_k"])
    return {
        "fold": fold["name"], "horizon_trading_days": horizon,
        "train_segment": [fold["train_start"], fold["train_end"]],
        "test_segment": [fold["test_start"], fold["test_end"]],
        "alpha_pool_size": int(pool.size),
        "alpha_pool": [{"expression": str(e), "weight": r4(w)} for e, w in zip(exprs, weights)],
        "train_pool_ic": r4(pool.best_ic_ret),
        "alphagen": eval_matrix(score, actual, min_n, top_k),
        "baselines": {
            "ret_5d_pct": eval_matrix(mom5, actual, min_n, top_k),
            "ret_20d_pct": eval_matrix(mom20, actual, min_n, top_k),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request_path")
    ap.add_argument("--alphagen-root", required=True)
    args = ap.parse_args()
    req = load_json(ROOT / args.request_path)
    cfg = load_json(ROOT / req["config_path"])
    frame = load_panel(req["data_start"], req["data_end"])
    api = import_alphagen(Path(args.alphagen_root).resolve())
    results, failures = [], []
    aggs = defaultdict(lambda: defaultdict(list))
    for fold in req["folds"]:
        for h in cfg["horizons_trading_days"]:
            try:
                out = run_one(frame, fold, int(h), cfg, api)
                results.append(out)
                aggs[int(h)]["ag_ic"].append(out["alphagen"]["mean_rank_ic"])
                aggs[int(h)]["ag_top1"].append(out["alphagen"]["top1_mean_forward_pct"])
                aggs[int(h)]["ag_top3"].append(out["alphagen"]["top3_mean_forward_pct"])
                for b in req["baselines"]:
                    aggs[int(h)][f"{b}_ic"].append(out["baselines"][b]["mean_rank_ic"])
                    aggs[int(h)][f"{b}_top1"].append(out["baselines"][b]["top1_mean_forward_pct"])
            except Exception as exc:
                failures.append({"fold": fold["name"], "horizon": int(h), "error": f"{type(exc).__name__}: {exc}"})
    summary = {}
    stable_h = 0
    for h in cfg["horizons_trading_days"]:
        h = int(h); v = aggs[h]
        clean = lambda xs: [float(x) for x in xs if x is not None and math.isfinite(float(x))]
        ag = clean(v["ag_ic"]); b5 = clean(v["ret_5d_pct_ic"]); b20 = clean(v["ret_20d_pct_ic"])
        a1 = clean(v["ag_top1"]); a3 = clean(v["ag_top3"])
        agm = float(np.mean(ag)) if ag else float("nan")
        b5m = float(np.mean(b5)) if b5 else float("nan"); b20m = float(np.mean(b20)) if b20 else float("nan")
        positive = sum(x > 0 for x in ag)
        beats = len(ag) == 3 and all(math.isfinite(x) for x in [agm, b5m, b20m]) and agm > max(b5m, b20m)
        stable = positive >= int(cfg["validation"]["required_positive_ic_folds"]) and beats
        stable_h += int(stable)
        summary[str(h)] = {
            "fold_count": len(ag), "alphagen_mean_rank_ic": r4(agm), "alphagen_positive_ic_folds": positive,
            "alphagen_mean_top1_forward_pct": r4(np.mean(a1)) if a1 else None,
            "alphagen_mean_top3_forward_pct": r4(np.mean(a3)) if a3 else None,
            "mom5_mean_rank_ic": r4(b5m), "mom20_mean_rank_ic": r4(b20m),
            "alphagen_minus_best_momentum_rank_ic": r4(agm - max(b5m, b20m)) if all(math.isfinite(x) for x in [agm, b5m, b20m]) else None,
            "screening_increment_stable": stable,
        }
    interpretation = "PROMISING_FOR_STAGE2" if stable_h >= int(cfg["validation"]["required_horizons_stable"]) and not failures else "NO_STABLE_INCREMENT_STAGE1"
    payload = {
        "schema_version": "1.0", "generated_at": now_utc(), "mode": req["mode"],
        "upstream_repository": cfg["upstream"]["repository"], "upstream_commit": cfg["upstream"]["commit"],
        "upstream_license_status": cfg["upstream"]["license_status"],
        "execution_note": "Pinned upstream code was checked out temporarily for isolated research execution and was not vendored into this repository.",
        "fold_results": results, "aggregate_summary": summary,
        "failure_count": len(failures), "failures": failures,
        "research_interpretation": interpretation,
        "screening_note": "Stage1 passes only if both 5d and 10d horizons have at least 2/3 positive Rank-IC folds and average Rank IC exceeds both 5d and 20d momentum baselines. This is research screening, not a MASTER rule.",
        "decision_eligible": False, "trade_signal": None, "trial_confirm": None, "portfolio_target": None,
        "master_override": False, "historical_decision_prohibited": True,
        "interpretation_boundary": "Formulaic alpha evidence may only be considered after separate research-integration review; this output cannot directly create risk permission, opportunity status, amount, holding reduction or exit.",
    }
    atomic_json_write(OUT_PATH, payload)
    status = {
        "generated_at": payload["generated_at"], "status": "PASS" if not failures else "DEGRADED",
        "research_interpretation": interpretation, "fold_horizon_runs": len(results), "failure_count": len(failures),
        "upstream_commit": cfg["upstream"]["commit"], "license_status": cfg["upstream"]["license_status"],
        "decision_eligible": False, "trade_signal": None, "master_override": False,
    }
    atomic_json_write(STATUS_PATH, status)
    print(json.dumps(status, ensure_ascii=False))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
