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

def test_dual_source_universe_reports_source_and_hydration_latency(monkeypatch):
    monkeypatch.setattr(discovery, "fetch_broad_etf_spot", lambda: [{"market_id": 1, "code": "510001", "name": "east"}])
    monkeypatch.setattr(discovery, "fetch_hithink_etf_master", lambda: [
        {"market_id": 1, "code": "510001", "name": "east"},
        {"market_id": 0, "code": "159999", "name": "extra"},
    ])
    monkeypatch.setattr(discovery, "_hydrate_tencent_identities", lambda rows: (
        [{"market_id": 0, "code": "159999", "name": "extra"}],
        {"requested_identity_count": len(rows), "tencent_quote_count": len(rows), "tencent_failed_batch_count": 0, "tencent_failures": []},
    ))
    rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-23")
    assert len(rows) == 2
    assert set(meta["source_elapsed_seconds"]) == {"eastmoney", "hithink"}
    assert meta["source_join_elapsed_seconds"] >= max(meta["source_elapsed_seconds"].values())
    assert meta["supplemental_hydration_elapsed_seconds"] >= 0
