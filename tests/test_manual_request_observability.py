from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_query_context


class ManualRequestBoundedObservabilityTests(unittest.TestCase):
    def test_fast_path_trace_keeps_observed_identities_and_unknowns_explicit(self):
        trace = build_query_context.build_fast_path_latency(
            {
                "request_id": "manual-20260914-1",
                "requested_at_beijing": "2026-09-14T10:00:00+08:00",
                "account_fact_available_at_beijing": "2026-09-14T10:00:05+08:00",
                "refresh_request_id": "query-refresh-1",
                "minimum_legal_inputs_ready_at_beijing": "2026-09-14T10:00:30+08:00",
                "required_account_persistence_identity": "requests/live_snapshot/manual-20260914-1.json",
            },
            {"captured_at": "2026-09-14T10:00:20+08:00"},
            {"generated_at_beijing": "2026-09-14T10:00:35+08:00"},
            {
                "refresh_mode": "QUERY_TIME_REFRESH_FIRST",
                "decision_freshness": {"post_request": True},
                "quotes": [
                    {"object_code": "000001.SH", "data_time_beijing": "2026-09-14T10:00:18+08:00", "quality_status": "PASS"}
                ],
            },
            "2026-09-14T10:00:40+08:00",
        )

        self.assertEqual(trace["manual_request_identity"], "manual-20260914-1")
        self.assertEqual(trace["manual_request_received_at"], "2026-09-14T10:00:00+08:00")
        self.assertEqual(trace["screenshot_account_fact_available_at"], "2026-09-14T10:00:05+08:00")
        self.assertEqual(trace["market_refresh_identity"], "query-refresh-1")
        self.assertEqual(trace["first_qualified_market_fact_identity"], "000001.SH")
        self.assertEqual(trace["first_qualified_market_fact_as_of"], "2026-09-14T10:00:18+08:00")
        self.assertEqual(trace["minimum_legal_inputs_ready_at"], "2026-09-14T10:00:30+08:00")
        self.assertEqual(trace["required_account_persistence_identity"], "requests/live_snapshot/manual-20260914-1.json")
        self.assertTrue(trace["trace_metadata_is_non_authoritative"])
        self.assertFalse(trace["trace_metadata_is_decision_gate"])
        self.assertFalse(trace["business_semantics_changed"])

    def test_unobservable_product_phases_remain_unknown_not_inferred(self):
        trace = build_query_context.build_fast_path_latency(
            {},
            {},
            {},
            {"decision_freshness": {}, "quotes": []},
            "",
        )

        self.assertEqual(trace["manual_request_identity"], "UNKNOWN")
        self.assertEqual(trace["manual_request_received_at"], "UNKNOWN")
        self.assertEqual(trace["screenshot_account_fact_available_at"], "UNKNOWN")
        self.assertEqual(trace["market_refresh_identity"], "UNKNOWN")
        self.assertEqual(trace["first_qualified_market_fact_identity"], "UNKNOWN")
        self.assertEqual(trace["first_qualified_market_fact_as_of"], "UNKNOWN")
        self.assertEqual(trace["minimum_legal_inputs_ready_at"], "UNKNOWN")
        self.assertEqual(trace["final_answer_identity"], "UNKNOWN")
        self.assertEqual(trace["required_account_persistence_identity"], "UNKNOWN")
        self.assertIn("product_message_received_at", trace["unobservable_product_phases"])


if __name__ == "__main__":
    unittest.main()
