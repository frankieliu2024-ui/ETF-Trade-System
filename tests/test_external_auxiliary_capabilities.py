import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "research" / "external_auxiliary_capabilities.json"


class ExternalAuxiliaryCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_registry_is_non_authoritative(self):
        boundary = self.data["authority_boundary"]
        self.assertFalse(boundary["provider_priority_changed"])
        self.assertFalse(boundary["trading_authority_changed"])
        self.assertFalse(boundary["can_override_request_bound_facts"])
        self.assertFalse(boundary["can_generate_trade_action"])
        self.assertFalse(boundary["can_generate_trial_or_confirm"])
        self.assertFalse(boundary["can_set_amount"])
        self.assertFalse(boundary["can_create_security_identity"])

    def test_registry_is_static_config_not_state(self):
        self.assertTrue(str(REGISTRY.relative_to(ROOT)).startswith("config/research/"))
        self.assertNotIn("data/state/", str(REGISTRY))

    def test_status_vocabulary_and_degradation(self):
        allowed = set(self.data["observed_status_values"])
        self.assertEqual(
            allowed,
            {"TESTED_AVAILABLE", "ENDPOINT_EXISTS_BUT_EMPTY", "ENTITLEMENT_BLOCKED", "NOT_APPLICABLE", "UNTESTED"},
        )
        for item in self.data["capabilities"]:
            self.assertIn(item["observed_status"], allowed)
        self.assertIn("does not revoke", self.data["orchestration_contract"]["degradation_rule"])

    def test_parallelism_is_auxiliary_only(self):
        contract = self.data["orchestration_contract"]
        self.assertIn("read-only auxiliary evidence", contract["parallelism"])
        self.assertIn("remain single-chain", contract["parallelism"])
        self.assertEqual(
            contract["stages"],
            ["EVIDENCE_NEED_PLANNING", "CAPABILITY_MATCH", "AUXILIARY_EVIDENCE_FETCH", "EVIDENCE_SYNTHESIS"],
        )
        self.assertTrue(contract["stages_are_actor_execution_phases_not_persisted_state"])

    def test_every_capability_preserves_business_boundaries(self):
        prohibited = set(self.data["global_prohibitions"])
        self.assertIn("PROVIDER_PRIORITY", prohibited)
        self.assertIn("TRADE_ACTION_GENERATION", prohibited)
        self.assertIn("SECOND_DECISION_CHAIN", prohibited)
        self.assertIn("PERSIST_CALL_RESULTS_AS_CANONICAL_STATE", prohibited)
        for item in self.data["capabilities"]:
            self.assertIsInstance(item["parallel_safe"], bool)
            self.assertTrue(item["independent_fetch_group"])
            self.assertIsInstance(item["stop_when_sufficient"], bool)
            self.assertTrue(item["allowed_business_uses"])


if __name__ == "__main__":
    unittest.main()
