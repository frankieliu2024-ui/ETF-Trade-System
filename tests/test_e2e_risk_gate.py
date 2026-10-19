import unittest

from scripts.build_e2e_status import risk_component


class E2ERiskGateTests(unittest.TestCase):
    def test_stale_auxiliary_risk_evidence_does_not_clear_current_gross(self):
        result = risk_component(
            {"as_of_transaction_date": "2026-10-16", "summary": {"current_strategy_return_pct_gross": -1.0}, "generated_at": "2026-10-17T17:00:00+08:00"},
            {"market_date": "2026-10-19", "last_confirmed_market_date": "2026-10-16"},
            {
                "status": "DEGRADED",
                "data_alignment": "LATEST_AVAILABLE_RESEARCH_DATE",
                "risk_data_end": "2026-09-23",
                "current_market_date": "2026-10-17",
                "decision_block_reason": "risk_history_not_aligned_to_current_market_date",
            },
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["etf_strategy_risk_pct"], -1.0)
        self.assertEqual(result["source"], "data/state/etf_strategy_equity.json")


if __name__ == "__main__":
    unittest.main()
