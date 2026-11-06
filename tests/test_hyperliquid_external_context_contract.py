import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import hyperliquid_external_context as module


class HyperliquidExternalContextContractTests(unittest.TestCase):
    def _config(self, root: Path):
        config = root / "config" / "market"
        config.mkdir(parents=True)
        (config / "hyperliquid_etf_exposure_map.json").write_text(
            json.dumps({
                "focused_assets": ["xyz:DRAM", "io:SNDK"],
                "asset_mappings": [],
                "boundary": "read-only",
            }), encoding="utf-8"
        )

    def test_collection_and_provider_time_are_separate_and_activity_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root)
            responses = [
                [{"name": "xyz"}, {"name": "io"}],
                [{"universe": [{"name": "xyz:DRAM"}]}, [{"openInterest": "10", "dayNtlVlm": "20"}]],
                [{"universe": [{"name": "io:SNDK"}]}, [{"openInterest": "0", "dayNtlVlm": "0"}]],
            ]
            with patch.object(module, "CONFIG", root / "config" / "market" / "hyperliquid_etf_exposure_map.json"), patch.object(module, "_post", side_effect=responses):
                result = module.build()
            self.assertEqual(result["quality_status"], "DEGRADED")
            self.assertEqual(result["focused_count"], 2)
            self.assertIn("collection_started_at_utc", result)
            self.assertIn("collection_finished_at_utc", result)
            by_name = {x["asset"]["name"]: x for x in result["observations"]}
            self.assertEqual(by_name["xyz:DRAM"]["market_activity_status"], "ACTIVE")
            self.assertEqual(by_name["io:SNDK"]["market_activity_status"], "INACTIVE_OR_NO_ACTIVITY")
            self.assertEqual(by_name["xyz:DRAM"]["collected_at_utc"], by_name["xyz:DRAM"]["request_finished_at_utc"])
            self.assertEqual(by_name["xyz:DRAM"]["provider_timestamp_status"], "NOT_PROVIDED_BY_ENDPOINT")
            self.assertIn("context_id", result)
            self.assertTrue(result["context_id"].startswith("hl-"))
            self.assertEqual(result["earliest_observation_collected_at_utc"], by_name["xyz:DRAM"]["collected_at_utc"])
            self.assertEqual(result["latest_observation_collected_at_utc"], by_name["io:SNDK"]["collected_at_utc"])
            self.assertEqual(result["observation_count"], 2)
            self.assertIn("latest state visible to this run", result["time_basis"])

    def test_universe_context_length_mismatch_is_a_dex_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root)
            responses = [
                [{"name": "xyz"}],
                [{"universe": [{"name": "xyz:DRAM"}, {"name": "xyz:NVDA"}]}, [{"openInterest": "1", "dayNtlVlm": "1"}]],
            ]
            with patch.object(module, "CONFIG", root / "config" / "market" / "hyperliquid_etf_exposure_map.json"), patch.object(module, "_post", side_effect=responses):
                result = module.build()
            self.assertEqual(result["quality_status"], "FAILED")
            self.assertEqual(result["focused_count"], 0)
            self.assertEqual(result["dex_failures"][0]["dex"], "xyz")


if __name__ == "__main__":
    unittest.main()

