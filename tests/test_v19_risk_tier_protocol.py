import json
import unittest
from pathlib import Path

from scripts.check_production_mutation_protocol import (
    acceptance_matrix_for_tier,
    candidate_change_acceptance_allows_global_failure,
    classify_risk_tier,
    system_subtraction_fast_path_eligible,
    tier2_acceptance_route,
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
                ],
                important_runtime_contract=True,
            ),
            "TIER_2",
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

    def test_tier2_routes_preserve_fail_safe_fact_path(self):
        self.assertEqual(tier2_acceptance_route(projection_or_maintenance_only=True), "T2-P")
        self.assertEqual(
            tier2_acceptance_route(projection_or_maintenance_only=True, formal_decision_input=True),
            "T2-F",
        )
        self.assertEqual(
            tier2_acceptance_route(projection_or_maintenance_only=True, attribution_reliable=False),
            "T2-F",
        )
        self.assertIn("failure_attribution_on_hard_fail", acceptance_matrix_for_tier("TIER_2", "T2-F")["required_gates"])
        self.assertNotIn("merged_main_full_consistency", acceptance_matrix_for_tier("TIER_2", "T2-P")["required_gates"])

    def test_unrelated_review_failure_is_orthogonal_but_same_domain_fails_safe(self):
        failed = [{"name": "review:post_close_canonical_chain", "status": "FAIL"}]
        self.assertTrue(candidate_change_acceptance_allows_global_failure(
            changed_files=["docs/生产变更与并发写入协议_V1.0.md", "scripts/check_production_mutation_protocol.py"],
            failed_checks=failed,
        ))
        self.assertFalse(candidate_change_acceptance_allows_global_failure(
            changed_files=["scripts/build_post_market_review.py"],
            failed_checks=failed,
        ))
        self.assertFalse(candidate_change_acceptance_allows_global_failure(
            changed_files=["docs/生产变更与并发写入协议_V1.0.md"],
            failed_checks=[{"name": "account_fact:current_availability", "status": "FAIL"}],
        ))

    def test_v19_failure_attribution_matrix(self):
        base = ["scripts/decision_source.py"]
        self.assertTrue(candidate_change_acceptance_allows_global_failure(
            changed_files=base,
            failed_checks=[{"name": "x:market_data", "failure_domain": "market_data",
                            "attribution": "PREEXISTING_UNRELATED"}],
        ))
        self.assertTrue(candidate_change_acceptance_allows_global_failure(
            changed_files=base,
            failed_checks=[{"name": "x:review", "failure_domain": "review",
                            "attribution": "NEW_UNRELATED_DISCOVERY"}],
        ))
        self.assertFalse(candidate_change_acceptance_allows_global_failure(
            changed_files=base,
            failed_checks=[{"name": "x:any", "attribution": "INTRODUCED_BY_CURRENT_CHANGE"}],
        ))
        self.assertFalse(candidate_change_acceptance_allows_global_failure(
            changed_files=base,
            failed_checks=[{"name": "x:any", "attribution": "ATTRIBUTION_INCONCLUSIVE"}],
        ))
        self.assertFalse(candidate_change_acceptance_allows_global_failure(
            changed_files=["scripts/market_data.py"],
            failed_checks=[{"name": "x:market_data", "failure_domain": "market_data",
                            "attribution": "PREEXISTING_UNRELATED"}],
        ))
        self.assertTrue(candidate_change_acceptance_allows_global_failure(
            changed_files=base,
            failed_checks=[
                {"name": "x:a", "attribution": "PREEXISTING_UNRELATED"},
                {"name": "x:b", "attribution": "NEW_UNRELATED_DISCOVERY"},
            ],
        ))
        self.assertFalse(candidate_change_acceptance_allows_global_failure(
            changed_files=base,
            failed_checks=[
                {"name": "x:a", "attribution": "PREEXISTING_UNRELATED"},
                {"name": "x:b", "attribution": "INTRODUCED_BY_CURRENT_CHANGE"},
            ],
        ))

    def test_subtraction_fast_path_requires_all_safety_conditions(self):
        kwargs = dict(
            no_new_owner_state_workflow=True,
            no_consumer_removed=True,
            no_core_input_change=True,
            net_complexity_decreases=True,
            stable_regression_evidence=True,
        )
        self.assertTrue(system_subtraction_fast_path_eligible(**kwargs))
        kwargs["no_core_input_change"] = False
        self.assertFalse(system_subtraction_fast_path_eligible(**kwargs))

    def test_query_context_writer_contract_matches_request_bound_ownership(self):
        root = Path(__file__).resolve().parents[1]
        cfg = json.loads((root / "config/maintenance/production_mutation_protocol.json").read_text(encoding="utf-8"))
        contract = cfg["state_file_contracts"]["data/state/query_context.json"]
        writers = set(contract["allowed_writers"])
        self.assertNotIn(".github/workflows/overseas-preopen-pulse.yml", writers)
        pulse = (root / ".github/workflows/overseas-preopen-pulse.yml").read_text(encoding="utf-8")
        self.assertNotIn("scripts/build_query_context.py", pulse)
        self.assertNotIn("data/state/query_context.json", pulse)
        self.assertIn("formal requests rebuild query_context", pulse)

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
