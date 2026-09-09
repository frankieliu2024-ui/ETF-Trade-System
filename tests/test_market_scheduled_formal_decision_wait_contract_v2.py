from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScheduledFormalDecisionWaitContractV2Tests(unittest.TestCase):
    def test_scheduled_wait_is_state_driven_and_interactive_budget_is_unchanged(self):
        policy = json.loads((ROOT / "config/runtime_policy.json").read_text(encoding="utf-8"))
        scheduled = policy["scheduled_formal_decision"]
        interactive = policy["interactive_decision_freshness"]

        self.assertEqual(interactive["short_wait_budget_seconds"], 20)
        self.assertEqual(scheduled["wait_contract_version"], "2.1")
        self.assertEqual(scheduled["termination_basis"], "PRODUCER_STATE_AND_REQUIRED_DECISION_FACTS")
        self.assertTrue(scheduled["legacy_wait_mode_compatibility_only"])
        self.assertEqual(scheduled["max_wait_seconds"], 360)
        self.assertEqual(scheduled["safety_cap_source"], "scheduled_formal_decision.max_wait_seconds")
        self.assertEqual(scheduled["safety_cap_semantics"], "ACTOR_LEVEL_FINAL_GUARD_NOT_PRODUCER_TIMEOUT")
        self.assertTrue(scheduled["producer_timeout_inference_forbidden"])
        self.assertIn("CANONICAL_PRODUCER_QUEUED", scheduled["continue_wait_states"])
        self.assertIn("CANONICAL_PRODUCER_IN_PROGRESS", scheduled["continue_wait_states"])
        self.assertIn("CURRENT_READY_DECISION_CONTEXT_BUILDING", scheduled["continue_wait_states"])
        self.assertIn("CANONICAL_PRODUCER_FAILED", scheduled["terminal_failure_states"])
        self.assertIn("DECISION_CONTEXT_BUILD_FAILED", scheduled["terminal_failure_states"])
        self.assertEqual(scheduled["required_ready_state"], "PIT_CONSISTENT_DECISION_FACTS_READY")
        self.assertTrue(scheduled["require_final_current_and_decision_context_recheck"])
        self.assertTrue(scheduled["interactive_short_wait_unchanged"])

    def test_scheduled_rule_does_not_make_elapsed_time_or_workflow_timeout_primary(self):
        policy = json.loads((ROOT / "config/runtime_policy.json").read_text(encoding="utf-8"))
        rule = policy["scheduled_formal_decision"]["rule"]

        self.assertIn("producer-state", rule)
        self.assertIn("CURRENT已形成", rule)
        self.assertIn("必要decision context仍在构建时继续等待", rule)
        self.assertIn("Actor级最终安全上限", rule)
        self.assertIn("不是任何producer/workflow job timeout的镜像", rule)
        self.assertIn("interactive 20秒Fast Path保持不变", rule)


if __name__ == "__main__":
    unittest.main()
