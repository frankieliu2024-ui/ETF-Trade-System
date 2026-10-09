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
        self.assertFalse(state["analysis_eligible"])
        self.assertEqual(state["user_visible_output"], "SILENT_CONTINUATION")

    def test_real_recurrence_85s_prior_context_remains_silent_until_same_request_ready(self):
        parent = "formal-analysis-20261001-080100-bjt"
        building = classify_formal_request_consumer_state(
            parent,
            {
                "decision_fact_pack": {
                    "trigger": {"request_id": "manual-formal-20261001-075946"},
                    "formal_reply_freeze": {
                        "status": "READY",
                        "reply_freezable": True,
                        "request_id": "manual-formal-20261001-075946",
                    },
                }
            },
            producer_status="IN_PROGRESS",
        )
        self.assertEqual(building["status"], "REQUEST_BOUND_FACTS_BUILDING")
        self.assertTrue(building["continue_same_request"])
        self.assertFalse(building["reply_eligible"])
        self.assertEqual(building["user_visible_output"], "SILENT_CONTINUATION")

        ready = classify_formal_request_consumer_state(
            parent,
            {
                "decision_fact_pack": {
                    "trigger": {"request_id": parent},
                    "formal_reply_freeze": {
                        "status": "READY",
                        "reply_freezable": True,
                        "request_id": parent,
                        "blockers": [],
                    },
                }
            },
            producer_status="SUCCESS",
            canonical_persistence_status="PERSISTED",
        )
        self.assertEqual(ready["status"], "BUSINESS_DECISION_READY")
        self.assertTrue(ready["reply_eligible"])
        self.assertEqual(ready["user_visible_output"], "COMPLETE_BUSINESS_DECISION_ONLY")

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
            canonical_persistence_status="PERSISTED",
        )
        self.assertEqual(state["status"], "BUSINESS_DECISION_READY")
        self.assertFalse(state["continue_same_request"])
        self.assertTrue(state["reply_eligible"])
        self.assertTrue(state["analysis_eligible"])
        self.assertEqual(state["user_visible_output"], "COMPLETE_BUSINESS_DECISION_ONLY")


    def test_same_request_ready_without_bds_persistence_stays_silent_but_analysis_can_continue(self):
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
        self.assertEqual(state["status"], "BUSINESS_DECISION_PENDING_PERSISTENCE")
        self.assertTrue(state["analysis_eligible"])
        self.assertFalse(state["reply_eligible"])
        self.assertTrue(state["continue_same_request"])
        self.assertEqual(state["user_visible_output"], "SILENT_CONTINUATION")
        self.assertEqual(state["reason"], "BDS_CANONICAL_PERSISTENCE_NOT_CONFIRMED")

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
            canonical_persistence_status="PERSISTED",
        )
        # Persistence/projection/notification state is intentionally not an input.
        self.assertTrue(state["reply_eligible"])
        self.assertTrue(state["analysis_eligible"])
        self.assertEqual(state["user_visible_output"], "COMPLETE_BUSINESS_DECISION_ONLY")

    def test_stale_top_level_projection_cannot_make_current_request_ready(self):
        state = classify_formal_request_consumer_state(
            "current-parent",
            {
                # This projection is from an older request and must be ignored.
                "formal_reply_freeze": {
                    "status": "READY",
                    "reply_freezable": True,
                    "request_id": "old-parent",
                },
                "decision_fact_pack": {
                    "trigger": {"request_id": "current-parent"},
                    "formal_reply_freeze": {
                        "status": "IN_FLIGHT",
                        "reply_freezable": False,
                        "request_id": "current-parent",
                    },
                },
            },
            producer_status="RUNNING",
        )
        self.assertEqual(state["status"], "REQUEST_BOUND_FACTS_BUILDING")
        self.assertFalse(state["reply_eligible"])
        self.assertEqual(state["user_visible_output"], "SILENT_CONTINUATION")

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
        self.assertFalse(state["analysis_eligible"])
        self.assertEqual(state["user_visible_output"], "TERMINAL_FAILURE")


    def test_ready_facts_allow_reply_before_async_canonical_persistence(self):
        result = classify_formal_request_consumer_state(
            "request-1",
            {
                "decision_fact_pack": {
                    "trigger": {"request_id": "request-1"},
                    "formal_reply_freeze": {
                        "status": "READY",
                        "request_id": "request-1",
                        "reply_freezable": True,
                    },
                }
            },
            canonical_persistence_status="",
        )
        self.assertEqual(result["status"], "BUSINESS_DECISION_READY")
        self.assertTrue(result["analysis_eligible"])
        self.assertTrue(result["reply_eligible"])
        self.assertEqual(result["canonical_persistence_status"], "PENDING_ASYNC")
        self.assertEqual(result["user_visible_output"], "COMPLETE_BUSINESS_DECISION_ONLY")


if __name__ == "__main__":
    unittest.main()
