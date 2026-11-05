from scripts.build_query_context import _refresh_reused_discovery_external_evidence


def test_reused_discovery_refreshes_hyperliquid_evidence_without_rerunning_discovery():
    discovery = {
        "status": "READY",
        "candidates": [{
            "code": "159101",
            "name": "半导体ETF",
            "information_classes": ["PERSISTENT_STRUCTURE"],
            "external_driver_evidence": [],
        }],
    }
    external = {
        "quality_status": "PASS",
        "mapping": [{"asset": "SMH", "family": "SEMICONDUCTOR", "etf_codes": ["159101"]}],
        "observations": [{
            "asset": {"name": "SMH"},
            "dex": "xyz",
            "context": {"markPx": "110", "prevDayPx": "100", "funding": "0.00001", "premium": "0.0001"},
        }],
    }

    refreshed = _refresh_reused_discovery_external_evidence(discovery, external)
    candidate = refreshed["candidates"][0]
    assert candidate["external_driver_evidence"]
    assert "EXTERNAL_DRIVER_TRIGGER" in candidate["information_classes"]
    assert "_external_driver_triggers" not in candidate
    assert discovery["candidates"][0]["external_driver_evidence"] == []


