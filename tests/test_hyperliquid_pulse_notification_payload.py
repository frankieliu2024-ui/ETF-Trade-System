import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, "scripts")
from run_guarded_notification import _pulse_event, _pulse_test_candidates


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
        with patch(
            "run_guarded_notification._hyperliquid_relevant_asset_objects",
            return_value={"B": ["港股通创新药ETF工银（159217）"], "C": ["恒生科技ETF（513180）"]},
        ):
            event = _pulse_event("hyperliquid_pulse", "Hyperliquid", before, after)
        self.assertIsNotNone(event)
        self.assertLess(len(event["content"].encode("utf-8")), 10_000)
        self.assertIn("港股通创新药ETF工银（159217）", event["content"])
        self.assertIn("恒生科技ETF（513180）", event["content"])
        self.assertIn("资金费率0.1→0.2", event["content"])
        self.assertIn("间接风格或共同风险因子关系", event["content"])
        self.assertIn("信号含义：多空持仓成本发生变化", event["content"])
        for machine_label in ("资金费率", "未平仓量", "溢价"):
            assert machine_label not in event["content"]
        assert "外部参照" in event["content"]
        assert "下一步核对" in event["content"]
        self.assertIn("B → 港股通创新药ETF工银（159217）", event["content"])
        self.assertIn("对该ETF的当前影响", event["content"])
        self.assertIn("时机与风险过滤", event["content"])
        self.assertNotIn("C →", event["content"])
        self.assertIn("多空持仓成本", event["content"])
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
        self.assertNotIn("io:NVDA", event["content"])
        self.assertIn("科创50ETF（588000）", event["content"])
        self.assertNotIn("BTC", event["content"])

    def test_synthetic_pulse_exercises_discovery_and_hyperliquid_together(self):
        events = _pulse_test_candidates()
        self.assertEqual({event["source"] for event in events}, {"discovery_pulse", "hyperliquid_pulse"})
        hyperliquid = next(event for event in events if event["source"] == "hyperliquid_pulse")
        self.assertIn("恒生科技ETF（513180）", hyperliquid["content"])
        self.assertIn("创新药ETF（159992）", hyperliquid["content"])
        self.assertIn("资金费率", hyperliquid["content"])
        self.assertIn("未平仓量", hyperliquid["content"])
        self.assertIn("溢价", hyperliquid["content"])
        self.assertLess(len(hyperliquid["content"].encode("utf-8")), 8_000)
        self.assertNotIn("xyz:BABA", hyperliquid["content"])
        self.assertNotIn("xyz:XBI", hyperliquid["content"])


if __name__ == "__main__":
    unittest.main()

