import json
import unittest
from pathlib import Path


class V110GovernanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.protocol = (root / "docs/生产变更与并发写入协议_V1.0.md").read_text(encoding="utf-8")
        cls.mirror = json.loads(
            (root / "config/maintenance/production_mutation_protocol.json").read_text(
                encoding="utf-8"
            )
        )

    def test_protocol_and_machine_mirror_are_v110(self):
        self.assertIn("生产变更与并发写入协议 V1.10", self.protocol)
        self.assertEqual(self.mirror["normative_contract"]["version"], "V1.10")
        self.assertIn("V1.10", self.mirror["normative_contract"]["migration_note"])

    def test_failure_escalation_is_evidence_state_based(self):
        gate = self.mirror["failure_escalation_gate"]
        self.assertEqual(gate["basis"], "evidence_state_not_fixed_attempt_count")
        self.assertIn("runtime_object_transformation", gate["required_audit"])
        self.assertIn("canonical_owner", gate["required_audit"])
        self.assertTrue(gate["no_new_parallel_mechanism"])
        self.assertIn("Failure Escalation Gate", self.protocol)

    def test_historical_recovery_temporal_contract_separates_worlds(self):
        contract = self.mirror["historical_recovery_temporal_contract"]
        self.assertIn("source", contract["decision_time_proves"])
        self.assertIn("decision_work_package", contract["decision_time_proves"])
        self.assertIn("account", contract["decision_time_proves"])
        self.assertIn("current_canonical_writer_safety", contract["recovery_time_proves"])
        self.assertIn("duplicate_prevention", contract["recovery_time_proves"])
        self.assertIn("reconciliation", contract["recovery_time_proves"])
        self.assertIn("must_not_redefine_historical_decision_semantics", contract["prohibition"])
        self.assertEqual(contract["missing_historical_fact"], "FAIL_CLOSED")
        self.assertEqual(contract["ordinary_current_formal_decision"], "CURRENT_STATE_CONTRACT")
        self.assertIn("Historical Recovery Temporal Contract", self.protocol)

    def test_acceptance_is_orthogonal_and_readback_bound(self):
        contract = self.mirror["acceptance_orthogonality"]
        self.assertIn("safe_to_enter_main", contract["implementation_acceptance"])
        self.assertIn("real_target_path", contract["incident_closure_acceptance"])
        self.assertTrue(contract["local_pass_is_not_incident_closure"])
        self.assertTrue(contract["canonical_readback"] == "required_for_incident_closure")
        self.assertIn("Acceptance Orthogonality", self.protocol)

    def test_forensic_observability_remains_opt_in_not_a_fourth_governance_system(self):
        self.assertIn("ETF_FORMAL_REPLAY_FORENSIC=1", self.protocol)
        self.assertIn("opt-in", self.protocol)
        self.assertIn("process-local", self.protocol)
        self.assertIn("no_new_parallel_mechanism", self.mirror["failure_escalation_gate"])
        self.assertEqual(
            self.mirror["mirror_reduction"]["normative_text_source"],
            "docs/生产变更与并发写入协议_V1.0.md",
        )


if __name__ == "__main__":
    unittest.main()
