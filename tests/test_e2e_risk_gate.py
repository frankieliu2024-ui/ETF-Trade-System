import unittest

from scripts.build_e2e_status import risk_component


class E2ERiskGateTests(unittest.TestCase):
    def test_stale_canonical_risk_evidence_blocks_projection(self):
        result = risk_component(
            {"summary": {"current_strategy_return_pct_gross": -1.0}, "generated_at": "2026-10-17T17:00:00+08:00"},
            {"market_date": "2026-10-17"},
            {
                "status": "DEGRADED",
                "data_alignment": "LATEST_AVAILABLE_RESEARCH_DATE",
                "risk_data_end": "2026-09-23",
                "current_market_date": "2026-10-17",
                "decision_block_reason": "risk_history_not_aligned_to_current_market_date",
            },
        )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertIsNone(result["etf_strategy_risk_pct"])


if __name__ == "__main__":
    unittest.main()
