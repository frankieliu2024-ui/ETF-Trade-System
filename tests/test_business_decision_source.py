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
            "capital_competition": [],
            "next_unit_capital_use": "现金",
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

    def test_pit_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            validate_source(self.base(), expected_snapshot="other")

    def test_projection_is_deterministic_and_does_not_redecide(self):
        projected = build_formal_completion_from_source(self.base())
        self.assertEqual(projected["request_type"], STATE_SYNC_ONLY)
        self.assertEqual(projected["decision_id"], "d1")
        self.assertEqual(projected["next_unit_capital_use"], "现金")
        self.assertEqual(projected["projection_status"], "READY")

if __name__ == "__main__":
    unittest.main()
