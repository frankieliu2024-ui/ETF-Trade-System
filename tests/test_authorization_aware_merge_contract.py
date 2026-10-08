import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AuthorizationAwareMergeContractTests(unittest.TestCase):
    def test_protocol_declares_code_pr_only_merge_contract(self):
        cfg = json.loads(
            (ROOT / "config/maintenance/production_mutation_protocol.json").read_text(
                encoding="utf-8"
            )
        )
        contract = cfg["change_admission"]["merge_eligibility_contract"]
        self.assertEqual(contract["name"], "AUTHORIZATION_AWARE_CODE_PR_MERGE")
        self.assertIn("TIER_0", contract["auto_merge_policy"]["eligible_tiers"])
        self.assertIn("TIER_1", contract["auto_merge_policy"]["eligible_tiers"])
        self.assertIn("TIER_3", contract["auto_merge_policy"]["never_auto_merge"])
        self.assertIn("requests/live_snapshot_runtime_ingress", contract["does_not_apply_to"])
        self.assertEqual(
            contract["required_checks"]["jobs"],
            ["consistency", "production_acceptance"],
        )

    def test_protocol_is_fail_closed_for_ambiguous_authority(self):
        cfg = json.loads(
            (ROOT / "config/maintenance/production_mutation_protocol.json").read_text(
                encoding="utf-8"
            )
        )
        policy = cfg["change_admission"]["merge_eligibility_contract"]["auto_merge_policy"]
        self.assertIn("authorization_ambiguous", policy["never_auto_merge"])
        self.assertIn("UNKNOWN", policy["never_auto_merge"])
        self.assertIn("required_check_missing", cfg["change_admission"]["merge_eligibility_contract"]["fail_closed"])


if __name__ == "__main__":
    unittest.main()
