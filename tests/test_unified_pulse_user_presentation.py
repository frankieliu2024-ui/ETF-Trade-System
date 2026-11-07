import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "scripts")
from run_guarded_notification import _pulse_event


class UnifiedPulseUserPresentationTests(unittest.TestCase):
    def test_discovery_notification_is_short_and_explains_reason_or_boundary(self):
        before = {
            "candidates": [
                {"code": "159567", "name": "港股创新药ETF银华"},
            ]
        }
        after = {
            "formed_at_beijing": "2026-11-07T17:00:00+08:00",
            "candidates": [
                {"code": "159567", "name": "港股创新药ETF银华"},
                {
                    "code": "520550",
                    "name": "港股红利低波ETF招商",
                    "surfaced_states": [
                        {
                            "state": "PERSISTENT_TREND",
                            "evidence": {
                                "window_returns": {"5": 0.03, "20": 0.04},
                                "positive_windows": [5, 20],
                            },
                        }
                    ],
                    "discovery_spot": {"change_pct": 1.2, "turnover_pct": 8.1},
                },
            ],
        }
        event = _pulse_event("discovery_pulse", "Discovery", before, after)
        self.assertIsNotNone(event)
        self.assertIn("港股红利低波ETF招商", event["content"])
        self.assertIn("5日+3.0%", event["content"])
        self.assertIn("【发生了什么】", event["content"])
        self.assertIn("当前只提升下一次正式判断的比较优先级", event["content"])
        self.assertLess(len(event["content"]), 900)
        self.assertNotIn("前一时点", event["content"])
        self.assertNotIn("延续对象", event["content"])

    def test_hyperliquid_notification_aggregates_without_snapshot_dump(self):
        before = {
            "observations": [
                {"asset": "xyz:BABA", "funding": "0.010%", "open_interest": "100", "premium": "0.10%"},
                {"asset": "BTC", "funding": "0.010%", "open_interest": "100", "premium": "0.10%"},
            ]
        }
        after = {
            "formed_at_beijing": "2026-11-07T17:00:00+08:00",
            "observations": [
                {"asset": "xyz:BABA", "funding": "0.030%", "open_interest": "120", "premium": "0.40%"},
                {"asset": "BTC", "funding": "0.030%", "open_interest": "120", "premium": "0.40%"},
            ],
        }
        with patch(
            "run_guarded_notification._hyperliquid_relevant_asset_objects",
            return_value={"xyz:BABA": ["恒生科技ETF（513180）"]},
        ):
            event = _pulse_event("hyperliquid_pulse", "Hyperliquid", before, after)
        self.assertIsNotNone(event)
        self.assertIn("恒生科技ETF（513180）", event["content"])
        self.assertNotIn("资金费率", event["content"])
        self.assertNotIn("BTC", event["content"])
        self.assertNotIn("延续对象", event["content"])
        self.assertNotIn("前一时点", event["content"])
        self.assertIn("下一步核对", event["content"])
        self.assertIn("需复核", event["title"])


if __name__ == "__main__":
    unittest.main()
