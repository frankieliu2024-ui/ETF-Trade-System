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

    def test_downstream_state_sync_latency_does_not_degrade_decision_context(self):
        result = context_component(
            {
                "decision_fact_pack": {
                    "trigger": {
                        "request_id": "broker-sync",
                        "requested_at_beijing": "2026-09-30T13:27:00+08:00",
                    },
                    "formal_reply_freeze": {
                        "status": "NOT_APPLICABLE",
                        "reply_freezable": True,
                        "blockers": [],
                    },
                },
                "fast_path_latency": {
                    "latency_status": "DOWNSTREAM_NOT_DECISION_LATENCY",
                    "latency_observation_role": "DOWNSTREAM_COMPLETION_NOT_DECISION_REQUEST",
                    "may_measure_original_decision_latency": False,
                },
            },
            {
                "observability": {
                    "boundary": "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED"
                },
                "formal_intraday_context_completeness": {"status": "READY"},
            },
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["minimum_decision_context"], "READY_OR_NOT_APPLICABLE")
        self.assertEqual(result["formal_reply_gate"]["request_id"], "broker-sync")
        self.assertTrue(result["formal_reply_gate"]["reply_freezable"])

    def test_original_formal_request_missing_latency_still_degrades(self):
        result = context_component(
            {
                "decision_fact_pack": {
                    "trigger": {
                        "request_id": "formal-parent",
                        "requested_at_beijing": "2026-09-30T13:16:00+08:00",
                    }
                },
                "fast_path_latency": {
                    "latency_status": "INSTRUMENTATION_INCOMPLETE",
                    "latency_observation_role": "FORMAL_DECISION_REQUEST",
                    "may_measure_original_decision_latency": True,
                },
            },
            {"formal_intraday_context_completeness": {"status": "READY"}},
        )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["latency_status"], "INSTRUMENTATION_INCOMPLETE")
        self.assertEqual(result["latency_observation_role"], "FORMAL_DECISION_REQUEST")

    def test_request_bound_ready_exposes_compact_formal_reply_gate(self):
        result = context_component(
            {
                "decision_fact_pack": {
                    "trigger": {
                        "request_id": "r3",
                        "requested_at_beijing": "2026-09-29T01:16:00+08:00",
                    },
                    "formal_reply_freeze": {
                        "status": "READY",
                        "reply_freezable": True,
                        "blockers": [],
                    },
                },
                "fast_path_latency": {"latency_status": "OBSERVED"},
            },
            {"formal_intraday_context_completeness": {"status": "READY"}},
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["formal_reply_gate"]["request_id"], "r3")
        self.assertEqual(result["formal_reply_gate"]["status"], "READY")
        self.assertTrue(result["formal_reply_gate"]["reply_freezable"])
        self.assertEqual(result["formal_reply_gate"]["blockers"], [])
        manifest = result["formal_reply_gate"]["consumer_manifest"]
        self.assertEqual(manifest["request_id"], "r3")
        self.assertEqual(manifest["read_strategy"], "GATE_FIRST_TARGETED_PAYLOAD")
        self.assertEqual(
            manifest["large_payload_empty_read"],
            "REVERIFY_SAME_GITHUB_SOURCE_BEFORE_MISSING",
        )

    def test_request_bound_inflight_gate_remains_visible_when_context_degraded(self):
        result = context_component(
            {
                "decision_fact_pack": {
                    "trigger": {
                        "request_id": "r4",
                        "requested_at_beijing": "2026-09-29T01:16:00+08:00",
                    },
                    "formal_reply_freeze": {
                        "status": "IN_FLIGHT",
                        "reply_freezable": False,
                        "blockers": ["FORMAL_DISCOVERY_IN_FLIGHT"],
                    },
                },
                "fast_path_latency": {"latency_status": "BUILDING"},
            },
            {"formal_intraday_context_completeness": {"status": "READY"}},
        )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["formal_reply_gate"]["request_id"], "r4")
        self.assertFalse(result["formal_reply_gate"]["reply_freezable"])
        self.assertEqual(result["formal_reply_gate"]["blockers"], ["FORMAL_DISCOVERY_IN_FLIGHT"])

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
