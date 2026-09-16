from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScheduledFormalDecisionWaitContractV2Tests(unittest.TestCase):
    def test_scheduled_wait_and_interactive_wait_share_blocking_fact_principle(self):
        policy = json.loads((ROOT / "config/runtime_policy.json").read_text(encoding="utf-8"))
        scheduled = policy["scheduled_formal_decision"]
        interactive = policy["interactive_decision_freshness"]

        self.assertEqual(interactive["short_wait_budget_seconds"], 20)
        self.assertEqual(
            interactive["short_wait_semantics"],
            "PREFERRED_FAST_WINDOW_NOT_TERMINATION_BOUNDARY",
        )
        self.assertEqual(
            interactive["termination_basis"],
            "DECISION_BLOCKING_FACTS_AND_CANONICAL_CHAIN_STATE",
        )
        self.assertTrue(interactive["continue_wait_if_decision_blocking_fact_inflight"])
        self.assertTrue(interactive["fallback_requires_same_decision_market_phase"])
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
        self.assertFalse(scheduled["interactive_short_wait_unchanged"])

    def test_rules_do_not_make_interactive_20_seconds_a_forced_reply_boundary(self):
        policy = json.loads((ROOT / "config/runtime_policy.json").read_text(encoding="utf-8"))
        interactive_rule = policy["interactive_decision_freshness"]["rule"]
        scheduled_rule = policy["scheduled_formal_decision"]["rule"]

        self.assertIn("20秒只用于优先读取新CURRENT，不构成强制回复或终止边界", interactive_rule)
        self.assertIn("queued/in_progress/building", interactive_rule)
        self.assertIn("集合竞价快照不得替代连续竞价正式动作", interactive_rule)
        self.assertIn("关键事实仍在形成时提前结束", scheduled_rule)
        self.assertIn("各自现有合法终止边界", scheduled_rule)


if __name__ == "__main__":
    unittest.main()
