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

    def test_direct_mode_requires_discovery_object_and_market_date(self):
        for query in (
            {"market_date": "", "formal_etf_discovery": {"status": "READY"},
             "formal_reply_freeze": {"status": "NOT_APPLICABLE"}},
            {"market_date": "2026-11-03", "formal_etf_discovery": {},
             "formal_reply_freeze": {"status": "NOT_APPLICABLE"}},
            {"market_date": "2026-11-03", "formal_reply_freeze": {"status": "NOT_APPLICABLE"}},
        ):
            result = e2e.context_component(query, {"market_date": "2026-11-03"})
            self.assertEqual(result["status"], "BLOCKED")

    def test_incomplete_current_main_payload_is_not_direct_mode(self):
        result = e2e.context_component(
            {"market_date": "2026-11-03", "formal_etf_discovery": {"status": "READY"},
             "formal_reply_freeze": {"status": "NOT_APPLICABLE"}},
            {},
        )
        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_account_remains_execution_dependency_not_market_gate(self):
        account = e2e.account_component(
            {"status": "MISSING", "updated_at": None, "source": None},
            {"market_date": "2026-11-05", "needs_account_update": True},
        )
        self.assertEqual(account["status"], "BLOCKED")
        source = (ROOT / "scripts" / "build_e2e_status.py").read_text()
        self.assertIn('"account_dependency"', source)
        self.assertNotIn('formal_reply_gate["status"] = "BLOCKED"', source)

    def test_live_completion_bypasses_bds_and_market_snapshot_projection(self):
        sync_source = (ROOT / "scripts" / "process_state_sync_request.py").read_text()
        workflow_source = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text()
        completion_branch = sync_source.split("if completion_dispatch:", 1)[1].split(
            "# Explicit Business Decision Source dispatch.", 1
        )[0]
        self.assertIn('"bds_required": False', completion_branch)
        self.assertNotIn("build_formal_completion_from_source", completion_branch)
        self.assertNotIn("Project Formal Decision completion through market snapshot owner", workflow_source)


if __name__ == "__main__":
    unittest.main()

