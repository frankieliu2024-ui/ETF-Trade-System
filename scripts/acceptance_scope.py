"""Classify main changes for the single production acceptance contract.

This module is deliberately a pure path classifier. It has no state writes,
workflow dispatch, provider logic, or trading semantics.
"""
from __future__ import annotations

import argparse
import json
import re
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
UNKNOWN = "UNKNOWN"

FULL_ACCEPTANCE_CLASSES = frozenset({
    FORMAL_RULE_OR_CONFIG,
    ACCOUNT_FACT_MUTATION,
    TRADE_FACT_MUTATION,
    FORMAL_PROJECTION_MUTATION,
    REVIEW_EVENT_MUTATION,
    RESEARCH_FORMAL_MUTATION,
    STABLE_CODE_OR_WORKFLOW_CHANGE,
    UNKNOWN,
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

_GIT_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def normalize_git_path(path: str) -> str:
    """Normalize one path emitted by Git plumbing before classification.

    Git may quote non-ASCII paths and encode their UTF-8 bytes as octal escapes
    when core.quotePath is enabled. The production classifier must identify the
    underlying repository path rather than treating that transport rendering as
    a new UNKNOWN path class.
    """
    value = str(path).strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
        if _GIT_OCTAL_ESCAPE.search(value):
            raw = _GIT_OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)
            try:
                value = raw.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                value = raw
        value = value.replace(r"\\", "\\").replace(r'\"', '"')
    return value.strip().lstrip("./")


def classify_path(path: str) -> str:
    path = normalize_git_path(path)
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
    return UNKNOWN


def classify_paths(paths: Iterable[str]) -> dict:
    normalized = sorted({normalize_git_path(path) for path in paths if str(path).strip()})
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
