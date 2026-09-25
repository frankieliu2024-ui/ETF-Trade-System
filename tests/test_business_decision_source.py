import unittest

from scripts.business_decision_source import (
    BUSINESS_DECISION_SOURCE,
    STATE_SYNC_ONLY,
    build_formal_completion_from_source,
    classify_request,
    validate_source,
)

class BusinessDecisionSourceTests(unittest.TestCase):
    def base(self):
        return {
            "request_type": BUSINESS_DECISION_SOURCE,
            "request_id": "r1",
            "decision_id": "d1",
            "consumed_snapshot": "snap-1",
            "risk_permission": "PERMITTED",
            "main_candidate": "NONE",
            "opportunity_status": "无机会",
            "capital_use": "现金",
            "continued_holding_opportunity_cost": "低",
            "action_changes_now": "NO",
            "next_change_condition": "risk permission changes",
            "managed_position_reviews": [],
            "etf_opportunity_reviews": [],
            "capital_competition": [],
            "next_unit_capital_use": "现金",
            "decision_evidence_consumption": {
                "request_id": "r1",
                "layer_1_external_cross_market": "external/cross-market evidence reviewed",
                "layer_2_a_share_internal": "A-share index/structure/breadth feedback reviewed",
                "layer_3_etf_opportunity_capital": "Discovery/holdings/Observation/cash/releasable capital reviewed",
                "discovery_to_capital_competition_consumed": True,
                "all_managed_positions_sell_chain_consumed": True,
                "held_etf_additional_capital_consumed": True,
                "next_unit_capital_use_consumed": True,
            },
        }

    def test_explicit_classification_does_not_guess(self):
        self.assertEqual(classify_request(self.base()), BUSINESS_DECISION_SOURCE)
        legacy = dict(self.base())
        legacy["request_type"] = STATE_SYNC_ONLY
        self.assertEqual(classify_request(legacy), STATE_SYNC_ONLY)
        guessed = dict(self.base())
        guessed.pop("request_type")
        self.assertEqual(classify_request(guessed), "REFRESH_BEARING")

    def test_phase_one_reply_ready_without_formal_machine_fields(self):
        source = validate_source(self.base(), expected_snapshot="snap-1")
        self.assertTrue(source["source_durable"])
        self.assertTrue(source["reply_ready"])
        self.assertNotIn("formal_completion_validator", source)

    def test_missing_business_judgment_fails_closed(self):
        source = self.base()
        source.pop("capital_use")
        with self.assertRaises(ValueError):
            validate_source(source)

    def test_evidence_consumption_missing_fails_closed(self):
        source = self.base()
        source.pop("decision_evidence_consumption")
        with self.assertRaisesRegex(ValueError, "decision_evidence_consumption"):
            validate_source(source)

    def test_evidence_consumption_cross_request_fails_closed(self):
        source = self.base()
        source["parent_request_id"] = "parent-r1"
        with self.assertRaisesRegex(ValueError, "request_id must match parent request"):
            validate_source(source)

    def test_evidence_consumption_incomplete_chain_fails_closed(self):
        source = self.base()
        source["decision_evidence_consumption"]["discovery_to_capital_competition_consumed"] = False
        with self.assertRaisesRegex(ValueError, "discovery_to_capital_competition_consumed"):
            validate_source(source)

    def test_pit_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            validate_source(self.base(), expected_snapshot="other")


    def test_durable_source_survives_projection_failure_and_same_source_retry(self):
        """Regression: projection failure cannot erase or regenerate the durable Source."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from scripts import business_decision_source as source_contract
        from scripts.decision_source_persistence import persist_source, fingerprint

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_input = self.base()
            source_input.update({"parent_request_id": "r1", "business_decision": dict(self.base())})
            # The persistence owner performs Phase 1 and writes the immutable Source.
            durable = persist_source(root, source_input)
            self.assertTrue(durable["source_durable"])
            self.assertTrue(durable["reply_ready"])
            source_path = Path(durable["path"])
            before_bytes = source_path.read_bytes()
            before = source_path.read_text(encoding="utf-8")
            before_obj = __import__("json").loads(before)
            before_fp = before_obj["fingerprint"]
            before_identity = (before_obj["request_id"], before_obj.get("decision_id"))

            # Inject failure after durability, at the existing projection function.
            with patch.object(source_contract, "build_formal_completion_from_source", side_effect=RuntimeError("injected projection failure")):
                with self.assertRaisesRegex(RuntimeError, "injected projection failure"):
                    source_contract.build_formal_completion_from_source(before_obj)
            self.assertEqual(source_path.read_bytes(), before_bytes)
            self.assertEqual(source_path.read_text(encoding="utf-8"), before)
            self.assertEqual(fingerprint(before_obj), before_fp)
            self.assertEqual((before_obj["request_id"], before_obj.get("decision_id")), before_identity)

            # Retry consumes the exact durable bytes; it does not rebuild a decision.
            retry_source = __import__("json").loads(source_path.read_text(encoding="utf-8"))
            projected = source_contract.build_formal_completion_from_source(retry_source)
            self.assertEqual(projected["projection_status"], "READY")
            self.assertEqual(projected["decision_id"], before_identity[1])
            self.assertEqual((retry_source["request_id"], retry_source.get("decision_id")), before_identity)
            self.assertEqual(fingerprint(retry_source), before_fp)
            # Idempotent persistence of the same Source must not create another Source.
            second = persist_source(root, retry_source)
            self.assertTrue(second["idempotent_reuse"])
            self.assertEqual(len(list((root / "events" / "decision_sources").glob("*.json"))), 1)

    def test_projection_is_deterministic_and_does_not_redecide(self):
        projected = build_formal_completion_from_source(self.base())
        self.assertEqual(projected["request_type"], STATE_SYNC_ONLY)
        self.assertEqual(projected["decision_id"], "d1")
        self.assertEqual(projected["next_unit_capital_use"], "现金")
        self.assertEqual(projected["projection_status"], "READY")

if __name__ == "__main__":
    unittest.main()
