from __future__ import annotations

import unittest

from scripts.scheduled_review_completion import validate_formal_hyperliquid_time_binding


class FormalHyperliquidTimeBindingTests(unittest.TestCase):
    def test_rejects_ashare_time_presented_as_hyperliquid_observation(self):
        facts = {
            "fact_binding": {"captured_at_beijing": "2026-11-19T13:02:22+08:00"},
            "hyperliquid_external_driver_context": {
                "latest_observation_collected_at_beijing": "2026-11-19T14:25:24+08:00",
                "observations": [
                    {"observation_as_of": "2026-11-19T14:25:24+08:00"}
                ],
            },
        }
        content = "Hyperliquid最新可见观测约为2026-11-19T13:02:22+08:00。"
        with self.assertRaisesRegex(ValueError, "A-share"):
            validate_formal_hyperliquid_time_binding(facts, content)

    def test_accepts_hyperliquid_time_and_separate_ashare_boundary(self):
        facts = {
            "fact_binding": {"captured_at_beijing": "2026-11-19T13:02:22+08:00"},
            "hyperliquid_external_driver_context": {
                "latest_observation_collected_at_beijing": "2026-11-19T14:25:24+08:00",
                "observations": [
                    {"observation_as_of": "2026-11-19T14:25:24+08:00"}
                ],
            },
        }
        content = (
            "A股快照为2026-11-19T13:02:22+08:00；"
            "Hyperliquid最新采集边界为2026-11-19T14:25:24+08:00。"
        )
        validate_formal_hyperliquid_time_binding(facts, content)


if __name__ == "__main__":
    unittest.main()
