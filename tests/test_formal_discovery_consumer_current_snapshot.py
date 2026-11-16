from pathlib import Path


def test_formal_discovery_consumer_prefers_current_snapshot_bound_source():
    source = (Path(__file__).parents[1] / "scripts" / "build_state_context.py").read_text(encoding="utf-8")
    assert "query_discovery = query.get(\"formal_etf_discovery\")" in source
    assert "current_snapshot and discovery_snapshot != current_snapshot" in source
    assert "return max(candidates, key=generated_at)" in source


def test_formal_discovery_consumer_rejects_old_market_snapshot_fallback():
    source = (Path(__file__).parents[1] / "scripts" / "build_state_context.py").read_text(encoding="utf-8")
    assert "if not candidates:" in source
    assert "return None" in source
