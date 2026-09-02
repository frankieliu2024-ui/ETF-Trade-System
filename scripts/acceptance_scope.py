"""Classify main changes for the single production acceptance contract.

This module is deliberately a pure path classifier. It has no state writes,
workflow dispatch, provider logic, or trading semantics.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable

FORMAL_RULE_OR_CONFIG = "FORMAL_RULE_OR_CONFIG"
ACCOUNT_FACT_MUTATION = "ACCOUNT_FACT_MUTATION"
TRADE_FACT_MUTATION = "TRADE_FACT_MUTATION"
FORMAL_PROJECTION_MUTATION = "FORMAL_PROJECTION_MUTATION"
REVIEW_EVENT_MUTATION = "REVIEW_EVENT_MUTATION"
RESEARCH_FORMAL_MUTATION = "RESEARCH_FORMAL_MUTATION"
STABLE_CODE_OR_WORKFLOW_CHANGE = "STABLE_CODE_OR_WORKFLOW_CHANGE"
ORDINARY_MARKET_PULSE = "ORDINARY_MARKET_PULSE"
DERIVED_STATE_ONLY = "DERIVED_STATE_ONLY"
DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"

FULL_ACCEPTANCE_CLASSES = frozenset({
    FORMAL_RULE_OR_CONFIG,
    ACCOUNT_FACT_MUTATION,
    TRADE_FACT_MUTATION,
    FORMAL_PROJECTION_MUTATION,
    REVIEW_EVENT_MUTATION,
    RESEARCH_FORMAL_MUTATION,
    STABLE_CODE_OR_WORKFLOW_CHANGE,
})

DIAGNOSTIC_PREFIXES = (
    "data/state/maintenance_diagnostic.json",
    "data/state/workflow_failure_diagnostic.json",
    "data/state/restricted_rollback_plan.json",
)
ORDINARY_MARKET_PREFIXES = (
    "data/market/snapshots/",
    "data/state/CURRENT.json",
    "data/state/runtime_health.json",
    "data/state/overseas_runtime_health.json",
    "data/state/us_pulse_runtime_health.json",
)
FORMAL_FILES = frozenset({
    "ETF当前状态_DASHBOARD.md",
    "ETF市场行情档案_2026.md",
    "ETF交易复盘与经验库_2026.md",
})


def classify_path(path: str) -> str:
    path = path.strip().lstrip("./")
    if path == "ETF规则_MASTER.md" or path.startswith("config/"):
        return FORMAL_RULE_OR_CONFIG
    if path == "data/state/account_fact.json":
        return ACCOUNT_FACT_MUTATION
    if path.startswith("events/trades/") or path.startswith("requests/trade_fact_correction/"):
        return TRADE_FACT_MUTATION
    if path in FORMAL_FILES:
        return FORMAL_PROJECTION_MUTATION
    if path.startswith("events/reviews/") or path.startswith("post_market_review/"):
        return REVIEW_EVENT_MUTATION
    if path.startswith("requests/research_backfill/") or path.startswith("events/research/") or path.startswith("research/reports/"):
        return RESEARCH_FORMAL_MUTATION
    if path.startswith("scripts/") or path.startswith(".github/workflows/"):
        return STABLE_CODE_OR_WORKFLOW_CHANGE
    if path in DIAGNOSTIC_PREFIXES:
        return DIAGNOSTIC_ONLY
    if path.startswith(ORDINARY_MARKET_PREFIXES):
        return ORDINARY_MARKET_PULSE
    if path.startswith("data/state/"):
        return DERIVED_STATE_ONLY
    return DERIVED_STATE_ONLY


def classify_paths(paths: Iterable[str]) -> dict:
    normalized = sorted({str(path).strip() for path in paths if str(path).strip()})
    classes = sorted({classify_path(path) for path in normalized})
    return {
        "required": bool(set(classes) & FULL_ACCEPTANCE_CLASSES),
        "classes": classes,
        "paths": normalized,
        "full_acceptance_classes": sorted(FULL_ACCEPTANCE_CLASSES),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    paths = sys.stdin.read().splitlines() if args.stdin else args.paths
    print(json.dumps(classify_paths(paths), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
