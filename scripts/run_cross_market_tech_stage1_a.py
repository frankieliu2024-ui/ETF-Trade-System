from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

import run_cross_market_tech_stage1 as core

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "research/backtests/cross_market_tech_stage1_a_validation.json"
STATUS = ROOT / "data/state/cross_market_tech_stage1_a_status.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request_path")
    args = ap.parse_args()
    req = core.load_json(ROOT / args.request_path)
    cfg = core.load_json(ROOT / req["config_path"])
    target_codes = [x["code"] for x in cfg["targets"]]

    local = core.load_local_panel(req["data_start"], req["data_end"], target_codes)
    sox = core.fetch_yahoo_history(cfg["external_objects"]["SOX"]["symbol"], req["data_start"], req["data_end"])
    ndx = core.fetch_yahoo_history(cfg["external_objects"]["NDX"]["symbol"], req["data_start"], req["data_end"])
    panel = core.attach_external(local, sox, ndx)
    signals = list(cfg["candidate_signals"])
    stage_a = core.evaluate(panel, cfg, req["folds"], list(cfg["stage_a_controls"]), signals)
    passing = stage_a["passing_signals"]
    interpretation = "STAGE_A_PROMISING" if passing else "NO_STABLE_INCREMENT_BEYOND_LOCAL_MOMENTUM"

    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "RESEARCH_ONLY_CROSS_MARKET_TECH_STAGE1_A",
        "objective": cfg["objective"],
        "targets": cfg["targets"],
        "external_objects": cfg["external_objects"],
        "entry_rule": cfg["entry_rule"],
        "point_in_time_rule": cfg["point_in_time_rule"],
        "local_rows": int(len(local)),
        "external_rows": {"SOX": int(len(sox)), "NDX": int(len(ndx))},
        "stage_a": stage_a,
        "research_interpretation": interpretation,
        "stage_a_passing_signals": passing,
        "stage_b_required": bool(passing),
        "decision_eligible": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
        "master_override": False,
        "historical_decision_prohibited": True,
        "production_context_integration": False,
        "interpretation_boundary": "Stage A only tests incremental cross-market information beyond lagged local momentum. It cannot generate any trade action. Stage B share-flow control is mandatory before any research conversion discussion."
    }
    atomic_json_write(OUT, payload)
    status = {
        "generated_at": payload["generated_at"],
        "status": "PASS",
        "research_interpretation": interpretation,
        "stage_a_passing_signals": passing,
        "stage_b_required": bool(passing),
        "target_count": len(target_codes),
        "decision_eligible": False,
        "trade_signal": None,
        "master_override": False
    }
    atomic_json_write(STATUS, status)
    print(json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
