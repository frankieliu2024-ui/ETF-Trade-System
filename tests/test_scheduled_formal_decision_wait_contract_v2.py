from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScheduledFormalDecisionWaitContractV2Tests(unittest.TestCase):
    def test_scheduled_wait_remains_state_driven_and_interactive_20s_is_not_termination(self):
        policy = json.loads((ROOT / "config/runtime_policy.json").read_text(encoding="utf-8"))
        scheduled = policy["scheduled_formal_decision"]
        interactive = policy["interactive_decision_freshness"]

        self.assertEqual(interactive["short_wait_budget_seconds"], 20)
        self.assertEqual(
            interactive["short_wait_semantics"],
            "PREFERRED_FAST_WINDOW_NOT_TERMINATION_BOUNDARY",
        )
        self.assertTrue(interactive["continue_wait_if_decision_blocking_fact_inflight"])
        self.assertEqual(scheduled["wait_contract_version"], "2.1")
        self.assertEqual(scheduled["termination_basis"], "PRODUCER_STATE_AND_REQUIRED_DECISION_FACTS")
        self.assertTrue(scheduled["legacy_wait_mode_compatibility_only"])
        self.assertEqual(scheduled["max_wait_seconds"], 360)
        self.assertEqual(scheduled["safety_cap_source"], "scheduled_formal_decision.max_wait_seconds")
        self.assertIn("CANONICAL_PRODUCER_QUEUED", scheduled["continue_wait_states"])
        self.assertIn("CANONICAL_PRODUCER_IN_PROGRESS", scheduled["continue_wait_states"])
        self.assertIn("CURRENT_READY_DECISION_CONTEXT_BUILDING", scheduled["continue_wait_states"])
        self.assertIn("CANONICAL_PRODUCER_FAILED", scheduled["terminal_failure_states"])
        self.assertIn("DECISION_CONTEXT_BUILD_FAILED", scheduled["terminal_failure_states"])
        self.assertEqual(scheduled["required_ready_state"], "PIT_CONSISTENT_DECISION_FACTS_READY")
        self.assertTrue(scheduled["require_final_current_and_decision_context_recheck"])
        self.assertFalse(scheduled["interactive_short_wait_unchanged"])

    def test_scheduled_rule_keeps_independent_budget_but_not_conflicting_termination_semantics(self):
        policy = json.loads((ROOT / "config/runtime_policy.json").read_text(encoding="utf-8"))
        rule = policy["scheduled_formal_decision"]["rule"]

        self.assertIn("共享‘关键事实优先、canonical链状态驱动’的上位终止原则", rule)
        self.assertIn("独立执行预算与调用边界", rule)
        self.assertIn("interactive路径的20秒仅为快速读取窗口", rule)
        self.assertIn("关键事实仍在形成时提前结束", rule)


if __name__ == "__main__":
    unittest.main()
