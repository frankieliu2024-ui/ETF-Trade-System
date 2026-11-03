import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts import build_e2e_status as e2e


class FormalDecisionCurrentMainGateRepairTests(unittest.TestCase):
    def test_direct_current_main_context_is_not_blocked_by_old_request_gate(self):
        result = e2e.context_component(
            {"market_date": "2026-11-03", "formal_etf_discovery": {"status": "READY"},
             "formal_reply_freeze": {"status": "NOT_APPLICABLE"}},
            {"market_date": "2026-11-03"},
        )
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["direct_current_main"])
        self.assertFalse(result["request_bound"])
        self.assertIsNone(result["formal_reply_gate"])

    def test_request_bound_context_without_fact_pack_remains_blocked(self):
        result = e2e.context_component(
            {"market_date": "2026-11-03", "formal_etf_discovery": {"status": "READY"},
             "formal_reply_freeze": {"status": "IN_FLIGHT"}},
            {"market_date": "2026-11-03"},
        )
        self.assertEqual(result["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
