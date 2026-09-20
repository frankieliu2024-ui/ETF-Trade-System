from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_issue_687_discovery_latency_is_observability_only() -> None:
    discovery = (ROOT / "scripts/formal_etf_opportunity_discovery.py").read_text(encoding="utf-8")
    query = (ROOT / "scripts/build_query_context.py").read_text(encoding="utf-8")

    ast.parse(discovery)
    query_tree = ast.parse(query)

    imported_names = {
        alias.name
        for node in query_tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "time" in imported_names

    for field in (
        "broad_acquisition_elapsed_seconds",
        "prefilter_elapsed_seconds",
        "history_validation_elapsed_seconds",
    ):
        assert field in discovery

    for field in (
        "candidate_formal_quote_elapsed_seconds",
        "discovery_pipeline_elapsed_seconds",
    ):
        assert field in query

    assert "OBSERVABILITY_ONLY_NOT_DECISION_GATE" in discovery
    assert "OBSERVABILITY_ONLY_NOT_DECISION_GATE" in query

    # The instrumentation must not alter the bounded Discovery resource contract.
    assert "MAX_HISTORY_SUCCESS_BUDGET = 12" in discovery
    assert "MAX_HISTORY_ATTEMPT_MULTIPLIER = 3" in discovery
    assert "MAX_OBSERVATION_CANDIDATES = 12" in discovery
    assert "MAX_HISTORY_FETCH_WORKERS = 2" in discovery
    assert "ThreadPoolExecutor" in discovery
    assert "as_completed" in discovery
    assert "Consume completed work strictly in original queue order" in discovery
