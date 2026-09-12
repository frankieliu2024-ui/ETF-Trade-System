"""Bounded DeepSeek hypothesis miner for active-return regime research.

Research-only. It does not write canonical state, does not create trade actions,
and deliberately withholds the 2026 holdout from the LLM discovery payload.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from llm_research_adapter import run_json_research_task
except ModuleNotFoundError:
    from scripts.llm_research_adapter import run_json_research_task

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_RETURN = ROOT / "research/backtests/active_return_stage1_validation.json"
ALPHAGEN = ROOT / "research/backtests/alphagen_stage1_validation.json"

DISCOVERY_END = "2025-12-31"
BLIND_HOLDOUT_START = "2026-01-01"
BLIND_HOLDOUT_END = "2026-08-21"

REGIME_FEATURES = {
    "breadth_mom5_positive_ratio",
    "breadth_mom20_positive_ratio",
    "median_mom5_pct",
    "median_mom20_pct",
    "cross_section_dispersion_mom20_pct",
    "leader_laggard_mom20_spread_pct",
    "median_20d_drawdown_pct",
    "median_20d_volatility_pct",
    "median_volume_ratio_20d",
}
OPS = {"gt", "gte", "lt", "lte"}
BASE_EVIDENCE = {"active_migration_spread", "right_tail_holding"}
HORIZONS = {5, 10, 20}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fold_subset(block: dict[str, Any]) -> dict[str, Any]:
    """Expose only 2024-2025 discovery folds; never expose fold3/2026 outcomes."""
    out: dict[str, Any] = {}
    for horizon, metrics in block.items():
        folds = metrics.get("folds") or {}
        out[horizon] = {
            "fold1_2024": folds.get("fold1"),
            "fold2_2025": folds.get("fold2"),
        }
    return out


def build_discovery_input(active: dict[str, Any], alphagen: dict[str, Any]) -> dict[str, Any]:
    alpha_discovery = []
    for row in alphagen.get("fold_results") or []:
        segment = row.get("test_segment") or []
        if len(segment) == 2 and str(segment[1]) <= DISCOVERY_END:
            alpha_discovery.append({
                "horizon_trading_days": row.get("horizon_trading_days"),
                "test_segment": segment,
                "alphagen": row.get("alphagen"),
                "baselines": row.get("baselines"),
            })
    return {
        "task": "active_return_regime_hypothesis_discovery",
        "discovery_window": {"end": DISCOVERY_END, "uses_outcomes_for_research_discovery": True},
        "blind_holdout": {
            "start": BLIND_HOLDOUT_START,
            "end": BLIND_HOLDOUT_END,
            "withheld_from_llm": True,
        },
        "existing_active_return_evidence": {
            "method": active.get("method") or {},
            "active_migration_spread_discovery_folds": _fold_subset(active.get("active_migration_spread") or {}),
            "right_tail_holding_discovery_folds": _fold_subset(active.get("right_tail_holding") or {}),
            "leadership_continuation_discovery_folds": _fold_subset(active.get("leadership_continuation") or {}),
        },
        "existing_formulaic_alpha_search": {
            "purpose": "avoid duplicating AlphaGen formula search",
            "discovery_fold_results": alpha_discovery,
            "current_formal_interpretation": "NO_STABLE_INCREMENT_STAGE1",
        },
        "allowed_regime_features": sorted(REGIME_FEATURES),
        "allowed_operators": sorted(OPS),
        "allowed_base_evidence": sorted(BASE_EVIDENCE),
        "allowed_horizons": sorted(HORIZONS),
        "design_constraints": {
            "max_hypotheses": 3,
            "max_conditions_per_hypothesis": 3,
            "asset_specific_conditions_forbidden": True,
            "future_return_as_condition_forbidden": True,
            "hidden_score_forbidden": True,
            "trade_action_forbidden": True,
            "master_change_forbidden": True,
        },
        "blind_acceptance_contract": {
            "candidate_rule_frozen_before_holdout_read": True,
            "discovery_and_holdout_both_required": True,
            "minimum_holdout_observations": 15,
            "must_improve_or_stabilize_existing_base_evidence": True,
            "20bps_cost_required_for_active_migration": True,
            "single_asset_concentration_check_required": True,
            "no_production_integration_from_this_artifact": True,
        },
    }


def validate_hypothesis_output(obj: Any, cfg) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != {"hypotheses", "uncertainties"}:
        raise ValueError("hypothesis output top-level schema is invalid")
    hypotheses = obj["hypotheses"]
    uncertainties = obj["uncertainties"]
    if not isinstance(hypotheses, list) or not (1 <= len(hypotheses) <= 3):
        raise ValueError("hypotheses must contain 1..3 items")
    if not isinstance(uncertainties, list) or len(uncertainties) > 10:
        raise ValueError("invalid uncertainties")
    if not all(isinstance(x, str) and len(x) <= 500 for x in uncertainties):
        raise ValueError("invalid uncertainty item")

    expected = {
        "hypothesis_id", "base_evidence", "conditions", "horizons_trading_days",
        "economic_logic", "failure_regime", "falsification",
    }
    clean = []
    seen_ids = set()
    for item in hypotheses:
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("hypothesis schema is invalid")
        hid = item["hypothesis_id"]
        if not isinstance(hid, str) or not hid or len(hid) > 80 or hid in seen_ids:
            raise ValueError("invalid hypothesis_id")
        seen_ids.add(hid)
        if item["base_evidence"] not in BASE_EVIDENCE:
            raise ValueError("invalid base_evidence")
        conditions = item["conditions"]
        if not isinstance(conditions, list) or not (1 <= len(conditions) <= 3):
            raise ValueError("conditions must contain 1..3 items")
        clean_conditions = []
        used_features = set()
        for cond in conditions:
            if not isinstance(cond, dict) or set(cond) != {"feature", "op", "threshold"}:
                raise ValueError("condition schema is invalid")
            feature = cond["feature"]
            op = cond["op"]
            threshold = cond["threshold"]
            if feature not in REGIME_FEATURES or feature in used_features:
                raise ValueError("invalid or duplicate regime feature")
            if op not in OPS or not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
                raise ValueError("invalid condition operator or threshold")
            used_features.add(feature)
            clean_conditions.append({"feature": feature, "op": op, "threshold": float(threshold)})
        horizons = item["horizons_trading_days"]
        if not isinstance(horizons, list) or not horizons or len(horizons) > 3:
            raise ValueError("invalid horizons")
        horizons = sorted(set(int(x) for x in horizons))
        if any(x not in HORIZONS for x in horizons):
            raise ValueError("unsupported horizon")
        for key in ("economic_logic", "failure_regime", "falsification"):
            if not isinstance(item[key], str) or not item[key] or len(item[key]) > 700:
                raise ValueError(f"invalid {key}")
        clean.append({
            "hypothesis_id": hid,
            "base_evidence": item["base_evidence"],
            "conditions": clean_conditions,
            "horizons_trading_days": horizons,
            "economic_logic": item["economic_logic"],
            "failure_regime": item["failure_regime"],
            "falsification": item["falsification"],
        })

    return {
        "schema_version": "1.0",
        "status": "OK",
        "provider": cfg.provider,
        "model": cfg.model,
        "result": {
            "hypotheses": clean,
            "uncertainties": uncertainties,
            "discovery_cutoff": DISCOVERY_END,
            "blind_holdout": [BLIND_HOLDOUT_START, BLIND_HOLDOUT_END],
            "decision_eligible": False,
            "trade_signal": None,
            "master_override": False,
            "production_integration": False,
        },
        "uncertainties": uncertainties,
        "production_action": None,
        "formal_state_write": False,
    }


SYSTEM_PROMPT = """
You are a research-only alpha hypothesis generator for an ETF active-return study.
Your unique job is NOT to search arbitrary formulas and NOT to recommend trades.
AlphaGen already covers formulaic expression search. Instead, propose at most three
interpretable market-regime filters that may explain why already-validated active
migration or right-tail evidence works in some periods and fails in others.

Use only the supplied discovery-period evidence and only the allowlisted regime
features/operators. The 2026 holdout is blind and must not be guessed, referenced,
or optimized to. Every condition must be deterministic and code-testable. Do not
name securities, do not use future returns as conditions, do not create scores,
weights, portfolio targets, buy/sell instructions, Trial/Confirm, amounts, risk
permissions, or MASTER changes.

Return JSON only with exactly two top-level keys: hypotheses, uncertainties.
Each hypothesis must contain exactly: hypothesis_id, base_evidence, conditions,
horizons_trading_days, economic_logic, failure_regime, falsification.
Each condition must contain exactly: feature, op, threshold.
""".strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--active-return", type=Path, default=ACTIVE_RETURN)
    ap.add_argument("--alphagen", type=Path, default=ALPHAGEN)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--enable-once", action="store_true")
    ap.add_argument("--observability", action="store_true")
    args = ap.parse_args()

    active = load_json(args.active_return)
    alphagen = load_json(args.alphagen)
    discovery = build_discovery_input(active, alphagen)
    result = run_json_research_task(
        SYSTEM_PROMPT,
        discovery,
        validate_hypothesis_output,
        enable_once=args.enable_once,
        observability=args.observability,
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
