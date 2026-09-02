import unittest

from scripts.review_prerequisite_lifecycle import (
    TERMINAL_STATUS,
    build_unrecoverable_review_event,
    is_valid_unrecoverable_review_event,
    validate_unrecoverable_assessment,
)


def assessment(**overrides):
    value = {
        "trade_event_id": "trade-1",
        "review_target_market_date": "2026-09-01",
        "missing_prerequisite": "15:00 close snapshot",
        "recovery_target": "2026-09-01/close",
        "recovery_classification": "UNRECOVERABLE",
        "recovery_attempted": True,
        "provider": "approved-history-provider",
        "source_reference": "provider-response/2026-09-01",
        "historical_retrieved_at_beijing": "2026-09-02T12:00:00+08:00",
        "effective_market_time_beijing": "2026-09-01T15:00:00+08:00",
        "original_provider_observation_time_beijing": None,
        "required_coverage": {"count": 14, "symbols": ["000001.SH"]},
        "actual_coverage": {"count": 0, "symbols": []},
        "unrecoverable_reason": "approved providers only returned prior-day facts",
        "evidence_reference": "recovery-assessment-1",
    }
    value.update(overrides)
    return value


class ReviewPrerequisiteLifecycleTests(unittest.TestCase):
    def test_valid_unrecoverable_terminal_event(self):
        ok, reason = validate_unrecoverable_assessment(assessment(), "trade-1")
        self.assertTrue(ok, reason)
        event = build_unrecoverable_review_event(
            assessment(), request_id="request-1", created_at_beijing="2026-09-02T12:01:00+08:00"
        )
        self.assertEqual(event["review_unavailability"]["lifecycle_status"], TERMINAL_STATUS)
        self.assertFalse(event["review_unavailability"]["normal_case_generated"])
        self.assertTrue(is_valid_unrecoverable_review_event(event, "trade-1"))

    def test_empty_or_fake_terminal_evidence_is_rejected(self):
        for key in ("evidence_reference", "source_reference", "unrecoverable_reason"):
            with self.subTest(key=key):
                value = assessment(**{key: ""})
                ok, _ = validate_unrecoverable_assessment(value, "trade-1")
                self.assertFalse(ok)
        ok, _ = validate_unrecoverable_assessment(
            assessment(recovery_attempted=False), "trade-1"
        )
        self.assertFalse(ok)

    def test_unknown_and_partial_are_not_terminal(self):
        for classification in ("UNKNOWN", "PARTIALLY_RECOVERABLE"):
            ok, _ = validate_unrecoverable_assessment(
                assessment(recovery_classification=classification), "trade-1"
            )
            self.assertFalse(ok)

    def test_complete_coverage_cannot_be_terminal(self):
        ok, _ = validate_unrecoverable_assessment(
            assessment(actual_coverage={"count": 14, "symbols": []}), "trade-1"
        )
        self.assertFalse(ok)

    def test_terminal_does_not_claim_provider_observation(self):
        ok, _ = validate_unrecoverable_assessment(
            assessment(original_provider_observation_time_beijing="2026-09-01T15:00:00+08:00"),
            "trade-1",
        )
        self.assertFalse(ok)

    def test_future_valid_recovery_reopens_normal_path(self):
        terminal = build_unrecoverable_review_event(
            assessment(), request_id="request-1", created_at_beijing="2026-09-02T12:01:00+08:00"
        )
        self.assertTrue(is_valid_unrecoverable_review_event(terminal, "trade-1"))
        # A later canonical normal-review projection replaces the terminal event.
        normal = {
            "event_type": "FORMAL_POST_CLOSE_REVIEW",
            "market_date": "2026-09-01",
            "review": {"market_date": "2026-09-01"},
        }
        self.assertFalse(is_valid_unrecoverable_review_event(normal, "trade-1"))

    def test_replay_is_deterministic(self):
        first = build_unrecoverable_review_event(
            assessment(), request_id="request-1", created_at_beijing="2026-09-02T12:01:00+08:00"
        )
        second = build_unrecoverable_review_event(
            assessment(), request_id="request-1", created_at_beijing="2026-09-02T12:01:00+08:00"
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
