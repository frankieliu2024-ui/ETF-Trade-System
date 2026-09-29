from __future__ import annotations

import unittest

from scripts.formal_request_consumer import classify_formal_request_consumer_state


class FormalRequestConsumerLifecycleTests(unittest.TestCase):
    def test_prior_request_context_is_building_not_terminal(self):
        state = classify_formal_request_consumer_state(
            "new-parent",
            {
                "decision_fact_pack": {
                    "trigger": {"request_id": "old-parent"},
                    "formal_reply_freeze": {
                        "status": "READY",
                        "reply_freezable": True,
                        "request_id": "old-parent",
                    },
                }
            },
            producer_status="IN_PROGRESS",
        )
        self.assertEqual(state["status"], "REQUEST_BOUND_FACTS_BUILDING")
        self.assertTrue(state["continue_same_request"])
        self.assertFalse(state["reply_eligible"])
        self.assertFalse(state["analysis_eligible"])
        self.assertEqual(state["user_visible_output"], "SILENT_CONTINUATION")
        self.assertFalse(state["analysis_eligible"])
        self.assertEqual(state["user_visible_output"], "SILENT_CONTINUATION")

    def test_same_request_inflight_continues_without_duplicate_parent(self):
        state = classify_formal_request_consumer_state(
            "parent",
            {
                "decision_fact_pack": {
                    "trigger": {"request_id": "parent"},
                    "formal_reply_freeze": {
                        "status": "IN_FLIGHT",
                        "reply_freezable": False,
                        "request_id": "parent",
                        "blockers": ["REQUEST_SCOPED_PIT_IN_FLIGHT"],
                    },
                }
            },
            producer_status="RUNNING",
        )
        self.assertEqual(state["status"], "REQUEST_BOUND_FACTS_BUILDING")
        self.assertTrue(state["continue_same_request"])
        self.assertFalse(state["reply_eligible"])

    def test_same_request_ready_makes_business_reply_immediately_eligible(self):
        state = classify_formal_request_consumer_state(
            "parent",
            {
                "decision_fact_pack": {
                    "trigger": {"request_id": "parent"},
                    "formal_reply_freeze": {
                        "status": "READY",
                        "reply_freezable": True,
                        "request_id": "parent",
                        "blockers": [],
                    },
                }
            },
            producer_status="SUCCESS",
        )
        self.assertEqual(state["status"], "BUSINESS_DECISION_READY")
        self.assertFalse(state["continue_same_request"])
        self.assertTrue(state["reply_eligible"])
        self.assertTrue(state["analysis_eligible"])
        self.assertEqual(state["user_visible_output"], "COMPLETE_BUSINESS_DECISION_ONLY")

    def test_downstream_persistence_failure_does_not_revoke_ready_reply(self):
        state = classify_formal_request_consumer_state(
            "parent",
            {
                "decision_fact_pack": {
                    "trigger": {"request_id": "parent"},
                    "formal_reply_freeze": {
                        "status": "READY",
                        "reply_freezable": True,
                        "request_id": "parent",
                    },
                }
            },
            producer_status="SUCCESS",
        )
        # Persistence/projection/notification state is intentionally not an input.
        self.assertTrue(state["reply_eligible"])

    def test_explicit_terminal_failure_is_the_only_failure_exit(self):
        state = classify_formal_request_consumer_state(
            "parent",
            None,
            producer_status="FAILED",
            terminal_failure=True,
        )
        self.assertEqual(state["status"], "TERMINAL_FAILURE")
        self.assertFalse(state["continue_same_request"])
        self.assertFalse(state["reply_eligible"])


if __name__ == "__main__":
    unittest.main()
