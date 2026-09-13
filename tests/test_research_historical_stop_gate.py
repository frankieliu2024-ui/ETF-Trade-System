import unittest

from scripts.research_artifact_contract import validate_research_result


def evidence(status="LEGAL_COVERAGE_INSUFFICIENT"):
    return {
        "requested_objects": ["510300"],
        "granularity": "daily",
        "target_period": {"start": "2021-01-01", "end": "2026-09-13"},
        "capabilities_checked": ["hithink.history", "historical_market_fact_recovery"],
        "entries_attempted_or_consumed": ["hithink.history"],
        "coverage_result": {"objects": 1, "valid_days": 10},
        "common_window_result": {"start": "2021-01-01", "end": "2026-09-13"},
        "pit_boundary": {"fact_time": "2026-09-12T15:00:00+08:00", "retrieved_at": "2026-09-13T10:00:00+08:00"},
        "status": status,
        "evidence_id": "preflight-fixture-001",
    }


class HistoricalStopGateTest(unittest.TestCase):
    def test_missing_evidence_rejects_historical_stop(self):
        errors = validate_research_result({"stop_reason": "HISTORICAL_DATA_STOP"})
        self.assertIn("historical stop requires HISTORICAL_DATA_PREFLIGHT_EVIDENCE", errors)

    def test_existing_entry_must_be_checked_before_stop(self):
        result = {
            "stop_reason": "HISTORICAL_DATA_STOP",
            "historical_data_preflight_evidence": evidence("RECOVERY_AVAILABLE_CONTINUE"),
            "stop_reason_evidence_id": "preflight-fixture-001",
        }
        errors = validate_research_result(result)
        self.assertIn("recovery available cannot be a final historical stop", errors)

    def test_runtime_unavailable_is_distinct_from_capability_absent(self):
        result = {
            "stop_reason": "HISTORICAL_DATA_INSUFFICIENT",
            "historical_data_preflight_evidence": evidence("CAPABILITY_PRESENT_RUNTIME_UNAVAILABLE"),
            "stop_reason_evidence_id": "preflight-fixture-001",
        }
        self.assertEqual(validate_research_result(result), [])

    def test_daily_does_not_satisfy_minute_contract(self):
        item = evidence("LEGAL_COVERAGE_INSUFFICIENT")
        item["granularity"] = "daily"
        result = {
            "stop_reason": "HISTORICAL_DATA_STOP",
            "historical_data_preflight_evidence": item,
            "stop_reason_evidence_id": item["evidence_id"],
        }
        self.assertEqual(validate_research_result(result), [])

    def test_valid_stop_requires_identity_binding(self):
        item = evidence()
        result = {
            "stop_reason": "HISTORICAL_COVERAGE_INSUFFICIENT",
            "historical_data_preflight_evidence": item,
            "stop_reason_evidence_id": "wrong-id",
        }
        self.assertIn("stop reason must bind the preflight evidence_id", validate_research_result(result))


if __name__ == "__main__":
    unittest.main()
