from __future__ import annotations

import argparse
import json
import math
import os
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
OUT_PATH = ROOT / "research/backtests/qlib_stage1_validation.json"
STATUS_PATH = ROOT / "data/state/qlib_research_status.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def round4(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, 4) if math.isfinite(value) else None


def load_panel(start: str, end: str) -> pd.DataFrame:
    rows = []
    for path in sorted(DAILY_DIR.glob("*.json")):
        market_date = path.stem
        if market_date < start or market_date > end:
            continue
        payload = load_json(path)
        for item in payload.get("features") or []:
            rows.append({
                "datetime": pd.Timestamp(market_date),
                "instrument": str(item.get("code") or ""),
                "close": item.get("close"),
                "high": item.get("high"),
                "low": item.get("low"),
                "volume": item.get("volume"),
                "amount": item.get("amount"),
            })
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("No historical daily research facts found")
    for col in ["close", "high", "low", "volume", "amount"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["close"]).sort_values(["instrument", "datetime"]).reset_index(drop=True)


def build_features(raw: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    parts = []
    for _, g in raw.groupby("instrument", sort=True):
        g = g.sort_values("datetime").copy()
        close = g["close"]
        ret1 = close.pct_change() * 100.0
        g["ret_1d_pct"] = ret1
        for n in [5, 10, 20]:
            g[f"ret_{n}d_pct"] = close.pct_change(n) * 100.0
        g["volatility_5d_pct"] = ret1.rolling(5).std()
        g["volatility_20d_pct"] = ret1.rolling(20).std()
        g["drawdown_from_20d_high_pct"] = (close / g["high"].rolling(20).max() - 1.0) * 100.0
        g["range_1d_pct"] = (g["high"] - g["low"]) / close.replace(0, np.nan) * 100.0
        g["volume_ratio_5d_20d"] = g["volume"].rolling(5).mean() / g["volume"].rolling(20).mean().replace(0, np.nan)
        g["amount_ratio_5d_20d"] = g["amount"].rolling(5).mean() / g["amount"].rolling(20).mean().replace(0, np.nan)
        for h in horizons:
            g[f"label_{h}d_pct"] = (close.shift(-h) / close - 1.0) * 100.0
            g[f"label_date_{h}d"] = g["datetime"].shift(-h)
        parts.append(g)
    frame = pd.concat(parts, ignore_index=True)

    grouped = frame.groupby("datetime", sort=False)
    r1_mean = grouped["ret_1d_pct"].transform("mean")
    r1_std = grouped["ret_1d_pct"].transform(lambda s: s.std(ddof=0))
    frame["cross_section_ret1_z"] = (frame["ret_1d_pct"] - r1_mean) / r1_std.replace(0, np.nan)
    frame["cross_section_ret1_z"] = frame["cross_section_ret1_z"].fillna(0.0)
    frame["cross_section_ret5_rank_pct"] = grouped["ret_5d_pct"].rank(pct=True)
    frame["cross_section_ret20_rank_pct"] = grouped["ret_20d_pct"].rank(pct=True)
    return frame.sort_values(["datetime", "instrument"]).reset_index(drop=True)


class FrameDataset:
    """Minimal Qlib-compatible dataset adapter over our point-in-time feature frame."""

    def __init__(self, data: pd.DataFrame, segments: dict, feature_cols: list[str], label_col: str, label_date_col: str):
        self.data = data
        self.segments = segments
        self.feature_cols = feature_cols
        self.label_col = label_col
        self.label_date_col = label_date_col

    def prepare(self, segment, col_set="feature", data_key=None):
        if isinstance(segment, str):
            start, end = self.segments[segment]
            seg_name = segment
        else:
            start, end = segment
            seg_name = None
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        mask = self.data["datetime"].between(start_ts, end_ts)
        if seg_name in {"train", "valid"}:
            mask &= self.data[self.label_date_col].notna() & (self.data[self.label_date_col] <= end_ts)
        else:
            mask &= self.data[self.label_date_col].notna()
        sub = self.data.loc[mask].copy().dropna(subset=self.feature_cols + [self.label_col])
        idx = pd.MultiIndex.from_frame(sub[["datetime", "instrument"]], names=["datetime", "instrument"])
        if col_set == "feature":
            out = sub[self.feature_cols].copy()
            out.index = idx
            return out
        if isinstance(col_set, (list, tuple)) and "feature" in col_set and "label" in col_set:
            tuples = [("feature", col) for col in self.feature_cols] + [("label", self.label_col)]
            values = [sub[col].to_numpy() for col in self.feature_cols] + [sub[self.label_col].to_numpy()]
            return pd.DataFrame(np.column_stack(values), index=idx, columns=pd.MultiIndex.from_tuples(tuples))
        if col_set == "label":
            out = sub[[self.label_col]].copy()
            out.index = idx
            return out
        raise ValueError(f"Unsupported col_set={col_set}")


def spearman_by_date(pred: pd.Series, actual: pd.Series, min_n: int) -> dict:
    joined = pd.concat([pred.rename("pred"), actual.rename("actual")], axis=1).dropna()
    vals = []
    by_date = {}
    for dt, g in joined.groupby(level="datetime"):
        if len(g) < min_n or g["pred"].nunique() < 2 or g["actual"].nunique() < 2:
            continue
        ic = g["pred"].corr(g["actual"], method="spearman")
        if pd.notna(ic):
            vals.append(float(ic))
            by_date[str(pd.Timestamp(dt).date())] = round4(ic)
    return {
        "eligible_dates": len(vals),
        "mean_rank_ic": round4(np.mean(vals)) if vals else None,
        "median_rank_ic": round4(np.median(vals)) if vals else None,
        "positive_ic_rate": round4(np.mean(np.array(vals) > 0)) if vals else None,
        "by_date": by_date,
    }


def candidate_stats(score: pd.Series, actual: pd.Series) -> dict:
    joined = pd.concat([score.rename("score"), actual.rename("actual")], axis=1).dropna()
    top, bottom = [], []
    for _, g in joined.groupby(level="datetime"):
        if len(g) < 2:
            continue
        ordered = g.sort_values("score")
        bottom.append(float(ordered.iloc[0]["actual"]))
        top.append(float(ordered.iloc[-1]["actual"]))
    return {
        "eligible_dates": len(top),
        "top_candidate_mean_forward_pct": round4(np.mean(top)) if top else None,
        "top_candidate_positive_rate": round4(np.mean(np.array(top) > 0)) if top else None,
        "bottom_candidate_mean_forward_pct": round4(np.mean(bottom)) if bottom else None,
        "top_minus_bottom_mean_spread_pct_points": round4(np.mean(np.array(top) - np.array(bottom))) if top else None,
    }


def evaluate_score(score: pd.Series, actual: pd.Series, min_n: int) -> dict:
    return {**spearman_by_date(score, actual, min_n), **candidate_stats(score, actual)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    request = load_json(ROOT / args.request_path)
    config = load_json(ROOT / request["config_path"])
    horizons = [int(x) for x in config["horizons_trading_days"]]
    feature_cols = list(config["features"])
    frame = build_features(load_panel(request["data_start"], request["data_end"]), horizons)

    from qlib.contrib.model import gbdt as qlib_gbdt

    class NoopRecorder:
        @staticmethod
        def log_metrics(**kwargs):
            return None

    qlib_gbdt.R = NoopRecorder()
    model_cfg = config["model"]
    min_n = int(config["validation"]["minimum_cross_section_size"])
    folds_out = []
    aggregates = defaultdict(lambda: defaultdict(list))
    failures = []

    for fold in request["test_folds"]:
        for h in horizons:
            label_col = f"label_{h}d_pct"
            label_date_col = f"label_date_{h}d"
            segments = {
                "train": (fold["train_start"], fold["train_end"]),
                "valid": (fold["valid_start"], fold["valid_end"]),
                "test": (fold["test_start"], fold["test_end"]),
            }
            ds = FrameDataset(frame, segments, feature_cols, label_col, label_date_col)
            try:
                model = qlib_gbdt.LGBModel(
                    loss=model_cfg["loss"],
                    early_stopping_rounds=int(model_cfg["early_stopping_rounds"]),
                    num_boost_round=int(model_cfg["num_boost_round"]),
                    learning_rate=float(model_cfg["learning_rate"]),
                    num_leaves=int(model_cfg["num_leaves"]),
                    max_depth=int(model_cfg["max_depth"]),
                    min_data_in_leaf=int(model_cfg["min_data_in_leaf"]),
                    feature_fraction=float(model_cfg["feature_fraction"]),
                    bagging_fraction=float(model_cfg["bagging_fraction"]),
                    bagging_freq=int(model_cfg["bagging_freq"]),
                    seed=int(model_cfg["seed"]),
                    deterministic=True,
                    force_col_wise=True,
                    num_threads=2,
                )
                model.fit(ds, verbose_eval=0)
                pred = model.predict(ds, "test")
                test = ds.prepare("test", col_set=["feature", "label"])
                actual = test["label"].iloc[:, 0]
                q_metrics = evaluate_score(pred, actual, min_n)
                baseline_metrics = {}
                for baseline in request["baselines"]:
                    baseline_metrics[baseline] = evaluate_score(test["feature"][baseline], actual, min_n)
                feature_importance = {}
                if getattr(model, "model", None) is not None:
                    imp = model.model.feature_importance(importance_type="gain")
                    feature_importance = {k: round4(v) for k, v in sorted(zip(feature_cols, imp), key=lambda x: x[1], reverse=True)}
                folds_out.append({
                    "fold": fold["name"],
                    "horizon_trading_days": h,
                    "segments": segments,
                    "test_samples": int(len(actual)),
                    "qlib": q_metrics,
                    "baselines": baseline_metrics,
                    "feature_importance_gain": feature_importance,
                })
                aggregates[h]["qlib_ic"].append(q_metrics.get("mean_rank_ic"))
                aggregates[h]["qlib_spread"].append(q_metrics.get("top_minus_bottom_mean_spread_pct_points"))
                for baseline, metrics in baseline_metrics.items():
                    aggregates[h][f"{baseline}_ic"].append(metrics.get("mean_rank_ic"))
            except Exception as exc:
                failures.append({"fold": fold["name"], "horizon": h, "error": f"{type(exc).__name__}: {exc}"})

    summary = {}
    stable_horizons = 0
    for h in horizons:
        values = aggregates[h]
        clean = lambda xs: [float(x) for x in xs if x is not None and math.isfinite(float(x))]
        q_ic = clean(values["qlib_ic"])
        b5 = clean(values["ret_5d_pct_ic"])
        b20 = clean(values["ret_20d_pct_ic"])
        q_spread = clean(values["qlib_spread"])
        q_mean = float(np.mean(q_ic)) if q_ic else float("nan")
        b5_mean = float(np.mean(b5)) if b5 else float("nan")
        b20_mean = float(np.mean(b20)) if b20 else float("nan")
        positive_folds = sum(x > 0 for x in q_ic)
        beats_both = all(math.isfinite(x) for x in [q_mean, b5_mean, b20_mean]) and q_mean > max(b5_mean, b20_mean)
        stable = len(q_ic) >= 3 and positive_folds >= 2 and beats_both
        stable_horizons += int(stable)
        summary[str(h)] = {
            "fold_count": len(q_ic),
            "qlib_mean_rank_ic": round4(q_mean),
            "qlib_positive_ic_folds": positive_folds,
            "qlib_mean_top_minus_bottom_spread_pct_points": round4(np.mean(q_spread)) if q_spread else None,
            "mom5_mean_rank_ic": round4(b5_mean),
            "mom20_mean_rank_ic": round4(b20_mean),
            "qlib_minus_best_momentum_rank_ic": round4(q_mean - max(b5_mean, b20_mean)) if all(math.isfinite(x) for x in [q_mean, b5_mean, b20_mean]) else None,
            "screening_increment_stable": stable,
        }

    interpretation = "PROMISING_FOR_STAGE2" if stable_horizons == len(horizons) and not failures else "NO_STABLE_INCREMENT_STAGE1"
    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "QLIB_STAGE1_POINT_IN_TIME_VALIDATION",
        "upstream_commit": config["upstream"]["commit"],
        "model_class": config["model"]["class"],
        "feature_count": len(feature_cols),
        "horizons_trading_days": horizons,
        "fold_results": folds_out,
        "aggregate_summary": summary,
        "failure_count": len(failures),
        "failures": failures,
        "research_interpretation": interpretation,
        "screening_note": "PROMISING只表示两个预测周期均在至少2/3时间fold取得正Rank IC且平均Rank IC高于5日和20日动量基线；这是研究筛选标准，不是交易规则或MASTER门槛。",
        "decision_eligible": False,
        "trade_signal": None,
        "trial_confirm": None,
        "master_override": False,
        "historical_decision_prohibited": True,
        "interpretation_boundary": "本结果仅验证Qlib是否对ETF横截面机会排序提供稳定样本外增量。不得直接生成风险许可、机会状态、金额、持有、降低风险或退出。",
    }
    atomic_json_write(OUT_PATH, payload)
    atomic_json_write(STATUS_PATH, {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "QLIB_RESEARCH_POC",
        "status": "PASS" if not failures else "DEGRADED",
        "request_path": args.request_path,
        "upstream_commit": config["upstream"]["commit"],
        "model_class": config["model"]["class"],
        "fold_horizon_runs": len(folds_out),
        "failure_count": len(failures),
        "research_interpretation": interpretation,
        "decision_eligible": False,
        "trade_signal": None,
        "boundary": config["boundaries"]["description"],
    })
    print(json.dumps({"runs": len(folds_out), "failures": len(failures), "interpretation": interpretation}, ensure_ascii=False))
    return 0 if not failures and folds_out else 1


if __name__ == "__main__":
    raise SystemExit(main())
