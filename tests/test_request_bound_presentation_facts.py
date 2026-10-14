import unittest

from scripts.business_decision_source import _validate_request_bound_presentation_facts


class RequestBoundPresentationFactTests(unittest.TestCase):
    def binding(self, content):
        _validate_request_bound_presentation_facts(
            {
                "presentation_content": content,
                "request_bound_fact_binding": {
                    "snapshot_id": "data/market/snapshots/2026-10-14_093335.json",
                    "market_date": "2026-10-14",
                    "market_phase": "CONTINUOUS_MORNING",
                    "captured_at": "2026-10-14T09:33:35+08:00",
                },
            },
            expected_snapshot="data/market/snapshots/2026-10-14_093335.json",
            expected_market_date="2026-10-14",
            expected_market_phase="CONTINUOUS_MORNING",
            expected_captured_at="2026-10-14T09:33:35+08:00",
        )

    def test_rejects_previous_session_presentation(self):
        with self.assertRaises(ValueError):
            self.binding("行情：2026-10-13 15:05:25")

    def test_accepts_current_request_bound_presentation(self):
        self.binding("行情：2026-10-14 09:33:35+08:00；阶段：盘中")


if __name__ == "__main__":
    unittest.main()
