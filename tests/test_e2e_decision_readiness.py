from __future__ import annotations

import unittest

from scripts.build_e2e_status import context_component


class E2EDecisionReadinessTests(unittest.TestCase):
    def test_legacy_unestablished_boundary_is_diagnostic_only(self):
        result = context_component(
            {"request_id": "q"},
            {
                "observability": {
                    "boundary": "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED"
                }
            },
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["minimum_decision_context"], "READY_OR_NOT_APPLICABLE")
        self.assertEqual(
            result["observability_boundary"],
            "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED",
        )

    def test_request_bound_legacy_observability_is_diagnostic_only(self):
        result = context_component(
            {
                "decision_fact_pack": {
                    "trigger": {
                        "request_id": "r1",
                        "requested_at_beijing": "2026-09-22T12:30:00+08:00",
                    }
                }
            },
            {
                "observability": {
                    "boundary": "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED"
                }
            },
        )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["minimum_decision_context"], "INCOMPLETE")
        self.assertTrue(result["request_bound"])

    def test_request_bound_observed_latency_ignores_legacy_boundary(self):
        result = context_component(
            {
                "decision_fact_pack": {
                    "trigger": {
                        "request_id": "r2",
                        "requested_at_beijing": "2026-09-22T12:30:00+08:00",
                    }
                },
                "fast_path_latency": {
                    "latency_status": "OBSERVED",
                    "request_to_core_result_latency": 4.2,
                },
            },
            {
                "observability": {
                    "boundary": "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED"
                }
            },
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["minimum_decision_context"], "READY_OR_NOT_APPLICABLE")
        self.assertTrue(result["request_bound"])

    def test_true_incomplete_formal_context_remains_not_ready(self):
        result = context_component(
            {"request_id": "q"},
            {
                "observability": {
                    "boundary": "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED"
                },
                "formal_intraday_context_completeness": {
                    "status": "DECISION_CONTEXT_INCOMPLETE"
                },
            },
        )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["minimum_decision_context"], "INCOMPLETE")

    def test_one_missing_context_remains_degraded(self):
        result = context_component({"request_id": "q"}, {})
        self.assertEqual(result["status"], "DEGRADED")

    def test_both_missing_contexts_remain_blocked(self):
        result = context_component({}, {})
        self.assertEqual(result["status"], "BLOCKED")

    def test_complete_context_remains_ready(self):
        result = context_component(
            {"request_id": "q"},
            {"formal_intraday_context_completeness": {"status": "READY"}},
        )
        self.assertEqual(result["status"], "READY")


if __name__ == "__main__":
    unittest.main()
