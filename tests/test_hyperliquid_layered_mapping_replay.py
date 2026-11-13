import json
import unittest
from pathlib import Path

from scripts.hyperliquid_contribution import _normalize_observations


ROOT = Path(__file__).resolve().parents[1]


class HyperliquidLayeredMappingReplayTests(unittest.TestCase):
    def test_context_mappings_do_not_become_etf_candidates(self):
        config = json.loads(
            (ROOT / "config/market/hyperliquid_etf_exposure_map.json").read_text(
                encoding="utf-8"
            )
        )
        mapping = config["asset_mappings"]
        context = {"mapping": mapping}
        observations = [
            {"asset": "xyz:EWY", "mark_px": "1", "prev_day_px": "1"},
            {"asset": "xyz:SP500", "mark_px": "1", "prev_day_px": "1"},
            {"asset": "xyz:DRAM", "mark_px": "1", "prev_day_px": "1"},
            {"asset": "xyz:CXMT", "mark_px": "1", "prev_day_px": "1"},
        ]

        rows = _normalize_observations(context, observations)
        by_asset = {row["asset"]: row for row in rows}

        self.assertEqual(by_asset["xyz:EWY"]["mapping_scope"], "REGIONAL_CONTEXT")
        self.assertEqual(by_asset["xyz:SP500"]["mapping_scope"], "MACRO_CONTEXT")
        self.assertEqual(by_asset["xyz:DRAM"]["mapping_scope"], "CONTEXT_ONLY")
        self.assertEqual(by_asset["xyz:CXMT"]["mapping_scope"], "CONTEXT_ONLY")

        for row in rows:
            self.assertFalse(row.get("mapped_assets"))
            self.assertFalse(row.get("etf_codes"))

    def test_no_broad_marker_fallback_remains(self):
        config = json.loads(
            (ROOT / "config/market/hyperliquid_etf_exposure_map.json").read_text(
                encoding="utf-8"
            )
        )
        for item in config["asset_mappings"]:
            self.assertIn(
                item.get("mapping_scope"),
                {"MACRO_CONTEXT", "REGIONAL_CONTEXT", "CONTEXT_ONLY",
                 "DIRECT_ETF", "INDUSTRY_CANDIDATE"},
            )


if __name__ == "__main__":
    unittest.main()
