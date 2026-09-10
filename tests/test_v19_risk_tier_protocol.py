import unittest

from scripts.check_production_mutation_protocol import (
    acceptance_matrix_for_tier,
    classify_risk_tier,
)


class V19RiskTierTests(unittest.TestCase):
    def test_shadow_examples(self):
        self.assertEqual(
            classify_risk_tier(
                changed_paths=["scripts/process_state_sync_request.py", "data/state/account_fact.json"],
                account_or_trade_fact=True,
            ),
            "TIER_3",
        )
        self.assertEqual(
            classify_risk_tier(
                changed_paths=[
                    "config/runtime_policy.json",
                    "scripts/post_close_review_due.py",
                    "scripts/check_system_consistency.py",
                ]
            ),
            "TIER_1",
        )
        self.assertEqual(
            classify_risk_tier(changed_paths=["docs/complexity-audit.md", "tests/test_protocol.py"]),
            "TIER_0",
        )
        self.assertEqual(
            classify_risk_tier(
                changed_paths=["scripts/check_production_mutation_protocol.py", "tests/test_protocol.py"]
            ),
            "TIER_1",
        )

    def test_behavior_semantic_prompt_does_not_remain_tier_zero(self):
        self.assertEqual(
            classify_risk_tier(
                changed_paths=["docs/scheduled-actor-prompt.md"],
                production_behavior_change=True,
            ),
            "TIER_1",
        )
        self.assertEqual(
            classify_risk_tier(
                changed_paths=["docs/scheduled-actor-prompt.md"],
                production_behavior_change=True,
                important_runtime_contract=True,
            ),
            "TIER_2",
        )
        self.assertEqual(
            classify_risk_tier(
                changed_paths=["docs/scheduled-actor-prompt.md"],
                production_behavior_change=True,
                pit_or_freshness=True,
            ),
            "TIER_3",
        )

    def test_ambiguous_and_high_risk_changes_fail_safe(self):
        self.assertEqual(classify_risk_tier(changed_paths=["UNKNOWN/changed"]), "TIER_3")
        self.assertEqual(
            classify_risk_tier(
                changed_paths=["scripts/runtime.py"],
                workflow_topology=True,
            ),
            "TIER_3",
        )

    def test_acceptance_matrices_are_minimal_and_escalating(self):
        self.assertEqual(
            acceptance_matrix_for_tier("TIER_0")["required_gates"],
            ["docs_or_format_check", "ordinary_ci"],
        )
        self.assertNotIn("merged_main_full_consistency", acceptance_matrix_for_tier("TIER_1")["required_gates"])
        self.assertIn("candidate_acceptance", acceptance_matrix_for_tier("TIER_2")["required_gates"])
        self.assertIn("failure_attribution", acceptance_matrix_for_tier("TIER_3")["required_gates"])


if __name__ == "__main__":
    unittest.main()
