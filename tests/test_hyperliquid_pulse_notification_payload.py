import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, "scripts")
from run_guarded_notification import _pulse_event


class HyperliquidPulseNotificationPayloadTests(unittest.TestCase):
    def test_first_snapshot_only_establishes_baseline(self):
        event = _pulse_event(
            "hyperliquid_pulse",
            "Hyperliquid",
            {},
            {"observations": [{"asset": "A"}]},
        )
        self.assertIsNone(event)

    def test_user_payload_contains_only_comparable_changed_assets(self):
        before = {
            "identity": "before",
            "observations": [{"asset": "B", "funding": "0.1", "open_interest": "1", "premium": "0.1"}],
        }
        after = {
            "identity": "after",
            "formed_at_beijing": "2026-11-07T15:00:00+08:00",
            "observations": [
                {"asset": "B", "funding": "0.2", "open_interest": "1", "premium": "0.1"},
                {"asset": "C", "funding": "0.1", "open_interest": "1", "premium": "0.1"},
            ],
        }
        event = _pulse_event("hyperliquid_pulse", "Hyperliquid", before, after)
        self.assertIsNotNone(event)
        self.assertLess(len(event["content"].encode("utf-8")), 10_000)
        self.assertIn("B", event["content"])
        self.assertIn("C", event["content"])
        self.assertNotIn("BTC", event["content"])

    def test_user_payload_does_not_leak_unmapped_venue_assets(self):
        before = {
            "identity": "before-scope",
            "observations": [
                {"asset": "io:NVDA", "funding": "0.1", "open_interest": "1", "premium": "0.1"},
                {"asset": "BTC", "funding": "0.1", "open_interest": "1", "premium": "0.1"},
            ],
        }
        after = {
            "identity": "after-scope",
            "formed_at_beijing": "2026-11-07T15:00:00+08:00",
            "observations": [
                {"asset": "io:NVDA", "funding": "0.2", "open_interest": "1", "premium": "0.1"},
                {"asset": "BTC", "funding": "0.2", "open_interest": "1", "premium": "0.1"},
            ],
        }
        with patch(
            "run_guarded_notification._hyperliquid_relevant_asset_objects",
            return_value={"io:NVDA": ["科创50ETF（588000）"]},
        ):
            event = _pulse_event("hyperliquid_pulse", "Hyperliquid", before, after)
        self.assertIsNotNone(event)
        self.assertIn("io:NVDA", event["content"])
        self.assertIn("科创50ETF（588000）", event["content"])
        self.assertNotIn("BTC", event["content"])


if __name__ == "__main__":
    unittest.main()
