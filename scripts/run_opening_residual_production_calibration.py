from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import run_low_cost_alpha_batch1 as b1
import run_cross_market_tech_stage1 as cross

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "research/backtests/opening_residual_561980_production_calibration.json"
CUTOFF = "2026-08-21"
CODE = "561980"


def r6(v):
    try:
        return round(float(v), 6)
    except Exception:
        return None


def main() -> int:
    df, _, _ = b1.load_panel()
    g = df[(df.code == CODE) & (df.date <= pd.Timestamp(CUTOFF))][["date", "code", "open", "close", "gap_pct"]].copy().sort_values("date")
    sox = cross.fetch_yahoo_history("^SOX", "2024-01-01", CUTOFF)
    ndx = cross.fetch_yahoo_history("^NDX", "2024-01-01", CUTOFF)
    ext = cross.attach_external(g[["date", "code", "open", "close"]], sox, ndx)
    g = g.merge(ext[["date", "code", "us_tech_equal_1d", "us_source_date"]], on=["date", "code"], how="left")
    train = g.dropna(subset=["gap_pct", "us_tech_equal_1d"]).copy()
    if len(train) < 400:
        raise RuntimeError(f"insufficient calibration rows: {len(train)}")
    X = np.column_stack([np.ones(len(train)), train.us_tech_equal_1d.to_numpy(float)])
    y = train.gap_pct.to_numpy(float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    expected = beta[0] + beta[1] * train.us_tech_equal_1d.to_numpy(float)
    residual = expected - y
    q = np.quantile(residual, [1/3, 2/3])
    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_OPENING_RESIDUAL_561980_PRODUCTION_CALIBRATION",
        "code": CODE,
        "name": "半导体设备ETF",
        "data_cutoff": CUTOFF,
        "calibration_rows": int(len(train)),
        "external_signal": "us_tech_equal_1d = equal-weight prior completed SOX and NDX cash-session 1d returns",
        "expected_gap_model": {
            "intercept_pct": r6(beta[0]),
            "beta_us_tech_equal_1d": r6(beta[1]),
            "formula": "expected_gap_pct = intercept_pct + beta_us_tech_equal_1d * us_tech_equal_1d_pct"
        },
        "continuous_evidence": {
            "formula": "opening_underpricing_residual_pct = expected_gap_pct - actual_gap_pct",
            "interpretation": "Positive means the A-share opening gap priced less overseas-tech strength / more overseas-tech weakness than the historical linear transmission estimate; negative means relatively richer opening pricing.",
            "historical_residual_q33_pct": r6(q[0]),
            "historical_residual_q67_pct": r6(q[1]),
            "threshold_not_a_trade_rule": True
        },
        "point_in_time": "Calibration uses only facts through 2026-08-21. Live evidence may use only the latest fully completed US cash session before the A-share open plus current A-share open and previous completed close.",
        "recalibration_rule": "Static calibration is valid as a descriptive research evidence transform after formal conversion; recalibration requires a separate robustness review and cannot be silently refit inside the live decision path.",
        "decision_eligible": False,
        "production_context_integration": False,
        "trade_signal": None,
        "master_override": False
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"PASS","calibration_rows":len(train),"intercept_pct":payload["expected_gap_model"]["intercept_pct"],"beta":payload["expected_gap_model"]["beta_us_tech_equal_1d"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
