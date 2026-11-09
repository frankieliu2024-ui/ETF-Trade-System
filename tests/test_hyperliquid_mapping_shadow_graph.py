import json
from pathlib import Path

from scripts.build_hyperliquid_mapping_shadow import build_shadow_mapping


def test_shadow_graph_covers_unmapped_objects_and_keeps_them_read_only():
    result = build_shadow_mapping(
        latest={
            "identity": "hl-test",
            "fact_time_beijing": "2026-11-09T20:34:00+08:00",
            "observations": [
                {"asset": "xyz:SMH", "object_name": "xyz:SMH"},
                {"asset": "xyz:NEW_OBJECT", "object_name": "xyz:NEW_OBJECT"},
            ],
        },
        mapping_config={
            "asset_mappings": [
                {
                    "asset": "xyz:SMH",
                    "family": "SEMICONDUCTOR",
                    "etf_codes": ["515880"],
                }
            ]
        },
        universe={"objects": [{"code": "515880", "name": "通信ETF"}]},
    )
    assert result["production_eligible"] is False
    assert result["observed_object_count"] == 2
    assert any(x["object_id"] == "xyz:NEW_OBJECT" and x["mapping_type"] == "UNMAPPED_OBJECT" for x in result["edges"])
    assert any(x["status"] == "ACTIVE_SEED" and x["etf_code"] == "515880" for x in result["edges"])


def test_marker_edges_remain_shadow_until_validated():
    result = build_shadow_mapping(
        latest={
            "context_id": "hl-test",
            "fact_time_beijing": "2026-11-09T20:34:00+08:00",
            "observations": [{"asset": "xyz:SMH"}],
        },
        mapping_config={
            "asset_mappings": [
                {
                    "asset": "xyz:SMH",
                    "family": "SEMICONDUCTOR",
                    "name_markers": ["半导体"],
                }
            ]
        },
        universe={"objects": [{"code": "515880", "name": "半导体ETF"}]},
    )
    edge = result["edges"][0]
    assert edge["mapping_type"] == "NAME_MARKER_INFERRED"
    assert edge["status"] == "SHADOW"
    assert edge["confidence"] == "LOW"
