import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_decision_ready_only_mode_reuses_enrichment_contracts():
    source = _source("scripts/build_state_context.py")
    tree = ast.parse(source)
    assert "--decision-ready-only" in source
    assert "canonical, read-only/enrichment facts" in source
    assert "research_context.json" in source
    assert "research_execution_summary.json" in source
    assert any(isinstance(node, ast.If) and "decision_ready_only" in ast.unparse(node.test) for node in ast.walk(tree))


def test_runtime_workflows_use_existing_canonical_builder_hot_path_mode():
    market_snapshot = _source(".github/workflows/market-snapshot.yml")
    opening_fallback = _source(".github/workflows/opening-auction-current-fallback.yml")
    assert "python scripts/build_state_context.py --decision-ready-only" not in market_snapshot
    assert "python scripts/build_state_context.py --decision-ready-only" in opening_fallback
    assert "build_state_context.py --fast" not in market_snapshot


def test_market_snapshot_does_not_rebuild_decision_context(self):
    workflow = _source(".github/workflows/market-snapshot.yml")
    assert "Build state context and candidates" not in workflow
    assert "build_state_context.py --decision-ready-only" not in workflow

def test_manual_close_recovery_does_not_reuse_request_bound_identity_or_pit():
    source = _source("scripts/build_state_context.py")
    assert 'parser.add_argument(\n        "--manual-close-recovery"' in source
    assert 'query_context = {} if manual_close_recovery else load_json' in source
    assert "request_time_text and not manual_close_recovery" in source


def test_phase3_does_not_add_fixed_minute_eligibility_or_parallel_owner():
    source = _source("scripts/build_state_context.py")
    assert "TTL" not in source
    assert "ThreadPoolExecutor" not in source
    assert "second decision" not in source.lower()


def test_discovery_reuse_remains_identity_bound():
    source = _source("scripts/build_query_context.py")
    for token in ("universe_identity", "discovery_delta_identity", "_reusable_formal_discovery"):
        assert token in source
    assert "same_market_date_same_latest_valid_node_same_discovery_delta_identity_same_universe_identity" in source
