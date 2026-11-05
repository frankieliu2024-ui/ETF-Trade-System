from pathlib import Path


def test_object_context_carries_hyperliquid_decision_evidence():
    text = (Path(__file__).parents[1] / "scripts" / "build_query_context.py").read_text(encoding="utf-8")
    assert '"decision_evidence": hyperliquid_decision_by_asset.get(asset_name) or {}' in text
    assert 'hyperliquid_decision_by_asset = {' in text
