import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, "scripts")
from run_guarded_notification import _pulse_event


class HyperliquidMappedScopeTests(unittest.TestCase):
    def test_unmapped_asset_does_not_create_user_event(self):
        before = {"observations": [{"asset": "BTC", "funding": "0.1"}]}
        after = {"observations": [{"asset": "BTC", "funding": "0.2"}]}
        with patch("run_guarded_notification._hyperliquid_relevant_asset_objects", return_value={}):
            self.assertIsNone(_pulse_event("hyperliquid_pulse", "Hyperliquid", before, after))

    def test_mapped_asset_keeps_only_current_object_scope(self):
        before = {"observations": [
            {"asset": "xyz:NVDA", "funding": "0.1"},
            {"asset": "BTC", "funding": "0.1"},
        ]}
        after = {"formed_at_beijing": "2026-11-07T16:00:00+08:00", "observations": [
            {"asset": "xyz:NVDA", "funding": "0.2"},
            {"asset": "BTC", "funding": "0.2"},
        ]}
        with patch("run_guarded_notification._hyperliquid_relevant_asset_objects", return_value={"xyz:NVDA": ["科创50ETF（588000）"]}):
            event = _pulse_event("hyperliquid_pulse", "Hyperliquid", before, after)
        self.assertIsNotNone(event)
        self.assertIn("科创50ETF（588000）", event["content"])
        self.assertNotIn("BTC", event["content"])


if __name__ == "__main__":
    unittest.main()
