import unittest

from scripts.build_skfolio_risk_evidence import risk_history_alignment


class RiskHistoryAlignmentTests(unittest.TestCase):
    def test_stale_history_is_not_currently_usable(self):
        self.assertEqual(
            risk_history_alignment("2026-09-23", "2026-10-17"),
            (False, "LATEST_AVAILABLE_RESEARCH_DATE"),
        )

    def test_same_market_date_is_usable(self):
        self.assertEqual(
            risk_history_alignment("2026-10-17", "2026-10-17"),
            (True, "ALIGNED_WITH_CURRENT_MARKET_DATE"),
        )


if __name__ == "__main__":
    unittest.main()
