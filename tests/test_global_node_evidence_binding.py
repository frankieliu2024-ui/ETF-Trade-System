from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_discovery_binds_a_share_and_hyperliquid_node_evidence():
    source = (ROOT / "scripts/build_weekly_discovery.py").read_text(encoding="utf-8")
    assert "hyperliquid_pulse_latest.json" in source
    assert "node_evidence_binding" in source
    assert "a_share_snapshot_identity" in source
    assert "hyperliquid_collection_time_beijing" in source
    assert 'external_driver_context={"latest": hyperliquid_latest' in source


def test_discovery_generation_identity_is_node_local():
    source = (ROOT / "scripts/build_weekly_discovery.py").read_text(encoding="utf-8")
    assert "DISCOVERY:{market_date}:{node}:{snapshot_identity}:{generated}" in source
    assert '"regeneration_required": True' in source
