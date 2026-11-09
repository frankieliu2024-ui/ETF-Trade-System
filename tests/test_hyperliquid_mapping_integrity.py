import json
from pathlib import Path

from scripts.hyperliquid_contribution import build_contribution


ROOT = Path(__file__).resolve().parents[1]


def test_mapping_catalog_has_unique_assets_and_integrity_contract():
    payload = json.loads((ROOT / "config/market/hyperliquid_etf_exposure_map.json").read_text())
    mappings = payload["asset_mappings"]
    assets = [row["asset"] for row in mappings]
    assert len(assets) == len(set(assets))
    assert payload["mapping_integrity"]["unique_asset_mapping_required"] is True
    assert payload["mapping_integrity"]["relationship_types"]


def test_latest_hyperliquid_projection_has_consumable_mapping_scope():
    latest = json.loads((ROOT / "data/state/hyperliquid_pulse_latest.json").read_text())["latest"]
    result = build_contribution(
        context={
            "latest": latest,
            "mapping": latest.get("mapping") or [],
            "observations": latest.get("observations") or [],
        },
        task="MAPPING_ACCEPTANCE",
    )
    assert result["as_of_beijing"]
    assert result["mapped_object_count"] > 0
    assert result["mapping_coverage_status"] == "LIVE_SIGNAL_MATCHED"
    for item in result["mapped_objects"]:
        assert item["asset"]
        assert item["family"]
        assert item["mapped_assets"] or item["mapping_reason"]
        assert item["observation_as_of"]
        assert item["decision_effect"]
