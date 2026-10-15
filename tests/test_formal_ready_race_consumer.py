from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from formal_request_consumer import classify_formal_request_consumer_state  # noqa: E402


class FormalReadyRaceConsumerTests(unittest.TestCase):
    def _ready_context(self):
        parent = "formal-current"
        return parent, {
            "decision_fact_pack": {
                "trigger": {"request_id": parent},
                "formal_reply_freeze": {
                    "request_id": parent,
                    "status": "READY",
                    "reply_freezable": True,
                },
                "decision_work_package": {
                    "problem_graph": [{"problem_id": "RISK_PERMISSION"}],
                    "response_contract": {"required": ["RISK_PERMISSION"]},
                },
            },
        }

    def test_same_request_ready_overrides_global_blocked_observability(self):
        parent, context = self._ready_context()
        result = classify_formal_request_consumer_state(parent, context)
        self.assertEqual(result["status"], "BUSINESS_DECISION_READY")
        self.assertTrue(result["reply_eligible"])
        self.assertEqual(result["actor_action"], "COMPLETE_BUSINESS_DECISION_NOW")

    def test_unbound_or_empty_context_remains_silent_wait(self):
        result = classify_formal_request_consumer_state(
            "formal-current",
            {"decision_fact_pack": {}},
            producer_status="IN_PROGRESS",
        )
        self.assertEqual(result["status"], "REQUEST_BOUND_FACTS_BUILDING")
        self.assertTrue(result["continue_same_request"])
        self.assertFalse(result["user_output_allowed"])
        self.assertEqual(result["actor_action"], "CONTINUE_SAME_REQUEST_SILENTLY")


if __name__ == "__main__":
    unittest.main()
