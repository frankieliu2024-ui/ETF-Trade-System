from __future__ import annotations

import importlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DEFAULT_CONFIG = ROOT / "config/research/kronos_poc.json"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))


def round4(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value, 4)


@dataclass
class KronosRuntime:
    predictor: object
    torch: object


class KronosResearchAdapter:
    """Thin, read-only adapter around upstream Kronos.

    It emits research evidence only. It deliberately has no API for position size,
    Trial/Confirm, order generation, or MASTER rule override.
    """

    def __init__(self, config_path: Path | str = DEFAULT_CONFIG, kronos_repo_dir: Path | str | None = None):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.repo_dir = Path(kronos_repo_dir or os.environ.get("KRONOS_REPO_DIR", ROOT / ".cache/Kronos")).resolve()
        self._runtime: KronosRuntime | None = None

    def _load_runtime(self) -> KronosRuntime:
        if self._runtime is not None:
            return self._runtime
        if not self.repo_dir.exists():
            raise RuntimeError(f"KRONOS_REPO_DIR not found: {self.repo_dir}")
        sys.path.insert(0, str(self.repo_dir))
        try:
            torch = importlib.import_module("torch")
            model_module = importlib.import_module("model")
            Kronos = getattr(model_module, "Kronos")
            KronosTokenizer = getattr(model_module, "KronosTokenizer")
            KronosPredictor = getattr(model_module, "KronosPredictor")
        except Exception as exc:
            raise RuntimeError(f"Unable to import pinned Kronos runtime: {exc}") from exc

        model_cfg = self.config["model"]
        tokenizer = KronosTokenizer.from_pretrained(model_cfg["tokenizer"])
        model = Kronos.from_pretrained(model_cfg["predictor"])
        predictor = KronosPredictor(
            model,
            tokenizer,
            device=model_cfg.get("device") or None,
            max_context=int(model_cfg.get("max_context", 512)),
        )
        self._runtime = KronosRuntime(predictor=predictor, torch=torch)
        return self._runtime

    @staticmethod
    def _validate_input(df: pd.DataFrame, x_timestamps: Iterable, y_timestamps: Iterable) -> tuple[pd.Series, pd.Series]:
        required = ["open", "high", "low", "close"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing OHLC columns: {missing}")
        if len(df) == 0:
            raise ValueError("Empty historical frame")
        if df[required].isnull().values.any():
            raise ValueError("Historical OHLC contains NaN")
        x_ts = pd.Series(pd.to_datetime(list(x_timestamps)))
        y_ts = pd.Series(pd.to_datetime(list(y_timestamps)))
        if len(x_ts) != len(df):
            raise ValueError("x_timestamps length does not match historical frame")
        return x_ts, y_ts

    def predict_distribution(self, df: pd.DataFrame, x_timestamps: Iterable, y_timestamps: Iterable, code: str, as_of: str) -> dict:
        runtime = self._load_runtime()
        forecast_cfg = self.config["forecast"]
        lookback = int(forecast_cfg.get("lookback", 256))
        pred_len = int(forecast_cfg.get("pred_len", 5))
        sample_paths = int(forecast_cfg.get("sample_paths", 5))
        if sample_paths < 1:
            raise ValueError("sample_paths must be >= 1")

        frame = df.tail(lookback).copy().reset_index(drop=True)
        x_ts_all = pd.Series(pd.to_datetime(list(x_timestamps)))
        x_ts = x_ts_all.tail(len(frame)).reset_index(drop=True)
        x_ts, y_ts = self._validate_input(frame, x_ts, y_timestamps)
        if len(y_ts) != pred_len:
            raise ValueError(f"Expected {pred_len} future timestamps, got {len(y_ts)}")

        last_close = float(frame.iloc[-1]["close"])
        terminal_returns: list[float] = []
        max_upside_returns: list[float] = []
        max_downside_returns: list[float] = []
        seed_base = int(forecast_cfg.get("seed", 20260824))

        for path_no in range(sample_paths):
            seed = seed_base + path_no
            np.random.seed(seed)
            runtime.torch.manual_seed(seed)
            pred = runtime.predictor.predict(
                df=frame,
                x_timestamp=x_ts,
                y_timestamp=y_ts,
                pred_len=pred_len,
                T=float(forecast_cfg.get("temperature", 1.0)),
                top_k=int(forecast_cfg.get("top_k", 0)),
                top_p=float(forecast_cfg.get("top_p", 0.9)),
                sample_count=1,
                verbose=False,
            )
            terminal_returns.append((float(pred.iloc[-1]["close"]) / last_close - 1.0) * 100.0)
            max_upside_returns.append((float(pred["high"].max()) / last_close - 1.0) * 100.0)
            max_downside_returns.append((float(pred["low"].min()) / last_close - 1.0) * 100.0)

        median_terminal = float(median(terminal_returns))
        bullish_cut = float(forecast_cfg.get("bullish_terminal_return_pct", 1.0))
        bearish_cut = float(forecast_cfg.get("bearish_terminal_return_pct", -1.0))
        bias = "bullish" if median_terminal >= bullish_cut else ("bearish" if median_terminal <= bearish_cut else "neutral")

        return {
            "schema_version": "1.0",
            "source": "Kronos",
            "source_role": "RESEARCH_EVIDENCE_ONLY",
            "code": str(code),
            "as_of": str(as_of),
            "input_rows": len(frame),
            "pred_len": pred_len,
            "sample_paths": sample_paths,
            "forecast_return_median_pct": round4(median_terminal),
            "forecast_up_probability": round4(sum(x > 0 for x in terminal_returns) / len(terminal_returns)),
            "forecast_downside_p10_pct": round4(percentile(max_downside_returns, 10)),
            "forecast_upside_p90_pct": round4(percentile(max_upside_returns, 90)),
            "forecast_terminal_volatility_pct": round4(float(np.std(terminal_returns, ddof=0))),
            "kronos_bias": bias,
            "terminal_return_paths_pct": [round4(x) for x in terminal_returns],
            "decision_eligible": False,
            "trade_signal": None,
            "trial_confirm": None,
            "master_override": False,
            "boundary": self.config["boundaries"]["description"],
        }
