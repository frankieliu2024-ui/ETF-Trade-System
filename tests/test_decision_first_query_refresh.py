import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import scripts.build_query_context as query_context


class DecisionFirstQueryRefreshTests(unittest.TestCase):
    def test_refresh_assurance_precedes_full_decision_context(self):
        events = []
        current = {
            "market_date": "2026-09-05",
            "latest_snapshot": "data/market/snapshots/2026-09-05_093000.json",
            "captured_at": "2026-09-05T09:30:05+08:00",
            "data_freshness": {"captured_at_beijing": "2026-09-05T09:30:05+08:00", "provider_as_of": "2026-09-05T09:30:05+08:00"},
            "rules_version": "V2.2.31",
        }
        account = {"status": "VALID", "updated_at": "2026-09-04T15:03:00+08:00", "source": "test"}
        decision = {"rules_version": "V2.2.31", "generated_at": "2026-09-05T09:30:06+08:00"}
        request = {"request_id": "r1", "requested_at_beijing": "2026-09-05T09:30:00+08:00", "requested_by": "interactive"}

        def fake_read_json(path, default=None):
            if str(path).endswith("request.json"):
                return request
            if str(path).endswith("etf_monitor_universe.json"):
                return {"version": "u1", "objects": [{"code": "159326"}]}
            return {} if default is None else default

        def fake_current(root):
            events.append("current")
            return dict(current)

        def fake_account(root):
            events.append("account")
            return dict(account)

        def fake_refresh(*args, **kwargs):
            events.append("refresh")
            return {
                "refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH",
                "decision_freshness": {"status": "DIRECT", "post_request": True, "resolved_post_request": True},
                "quotes": [],
            }

        def fake_decision(root):
            events.append("decision")
            return dict(decision)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "request.json").write_text("{}", encoding="utf-8")
            with patch.object(query_context, "read_json", side_effect=fake_read_json),                  patch.object(query_context, "read_current", side_effect=fake_current),                  patch.object(query_context, "read_account_fact", side_effect=fake_account),                  patch.object(query_context, "build_market_quote_context", side_effect=fake_refresh),                  patch.object(query_context, "build_decision_context", side_effect=fake_decision):
                result = query_context.build(root, request_file="request.json")

        self.assertEqual(events[0], "refresh")
        self.assertLess(events.index("refresh"), events.index("decision"))
        self.assertLess(events.index("refresh"), events.index("account"))
        self.assertEqual(result["freshness_assurance"]["refresh_mode"], "QUERY_TIME_IMMEDIATE_REFRESH")
        self.assertEqual(result["decision_fact_pack"]["current"]["latest_snapshot"], current["latest_snapshot"])

    def test_fact_pack_keeps_request_and_pit_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory),
                {"request_id": "r2", "requested_at_beijing": "2026-09-05T10:00:00+08:00", "requested_by": "scheduled"},
                {"market_date": "2026-09-05", "latest_snapshot": "snap.json", "captured_at": "2026-09-05T10:00:02+08:00"},
                {"status": "VALID", "updated_at": "2026-09-04T15:03:00+08:00", "source": "account"},
                {"rules_version": "V2.2.31", "generated_at": "2026-09-05T10:00:03+08:00"},
                {"refresh_mode": "CACHED_STATE", "decision_freshness": {"status": "DIRECT"}, "quotes": []},
            )
        self.assertEqual(pack["trigger"]["source"], "scheduled")
        self.assertEqual(pack["master"]["version"], "V2.2.31")
        self.assertEqual(pack["current"]["latest_snapshot"], "snap.json")
        self.assertEqual(pack["account_fact"]["as_of_beijing"], "2026-09-04T15:03:00+08:00")
        self.assertEqual(pack["etf_universe"]["count"], 0)

    def test_fact_pack_is_decision_ready_without_predeciding(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory),
                {"request_id": "r3", "requested_at_beijing": "2026-09-21T14:00:00+08:00"},
                {"market_date": "2026-09-21", "market_phase": "CONTINUOUS_AFTERNOON", "latest_snapshot": "snap.json"},
                {
                    "status": "VALID", "updated_at": "2026-09-21T13:59:00+08:00",
                    "deployable_cash": 18000,
                    "positions": [{"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF", "quantity": 100, "market_value": 1200}],
                },
                {
                    "rules_version": "V2.2.31", "generated_at": "2026-09-21T14:00:03+08:00",
                    "analysis_coverage": {"status": "PASS"},
                    "lifecycle_projection": {"561980": "HOLDING"},
                    "etf_strategy_risk_metrics": {"status": "READY"},
                },
                {
                    "refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH",
                    "decision_freshness": {"status": "DIRECT", "post_request": True},
                    "quotes": [{"symbol": "561980", "close": 1.2, "change_pct": 1.0, "data_time_beijing": "2026-09-21T14:00:02+08:00", "quality_status": "PASS", "provider": "test"}],
                },
                formal_discovery={"status": "PASS", "candidates": [{"code": "159995", "name": "芯片ETF", "eligibility": "EVALUATION"}]},
            )
        self.assertEqual(pack["role"], "PREFERRED_MINIMUM_SUFFICIENT_FORMAL_REASONING_INPUT")
        self.assertEqual(pack["account_fact"]["deployable_cash"], 18000)
        self.assertEqual(pack["account_fact"]["positions"][0]["code"], "561980")
        self.assertEqual(pack["qualified_market_facts"][0]["code"], "561980")
        self.assertEqual(pack["opportunity_inputs"]["candidates"][0]["code"], "159995")
        self.assertIn("EVERY_ACTUAL_POSITION_MANAGED_POSITION_REVIEW", pack["formal_reasoning_obligations"])
        self.assertIn("EXPLICIT_NEXT_UNIT_CAPITAL_USE", pack["formal_reasoning_obligations"])
        self.assertNotIn("main_candidate", pack)
        self.assertNotIn("recommended_action", pack)
        self.assertIn("must not select", pack["decision_boundary"])

    def test_fact_pack_degrades_capital_scope_when_discovery_not_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory),
                {"request_id": "r4", "requested_at_beijing": "2026-09-21T14:00:00+08:00"},
                {"market_date": "2026-09-21", "latest_snapshot": "snap.json"},
                {"status": "VALID", "positions": []},
                {"rules_version": "V2.2.31"},
                {"decision_freshness": {"post_request": True}, "quotes": []},
                formal_discovery={"status": "NOT_REQUESTED", "candidates": []},
            )
        readiness = pack["formal_reasoning_readiness"]
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["capital_efficiency_scope"], "DEGRADED_KNOWN_UNIVERSE")
        self.assertIn("FORMAL_DISCOVERY_NOT_RESOLVED", readiness["capital_efficiency_limitations"])

    def test_fact_pack_allows_resolved_discovery_with_zero_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory),
                {"request_id": "r5", "requested_at_beijing": "2026-09-21T14:00:00+08:00"},
                {"market_date": "2026-09-21", "latest_snapshot": "snap.json"},
                {"status": "VALID", "positions": []},
                {"rules_version": "V2.2.31"},
                {"decision_freshness": {"resolved_post_request": True}, "quotes": []},
                formal_discovery={"status": "PASS", "candidates": []},
            )
        readiness = pack["formal_reasoning_readiness"]
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["capital_efficiency_scope"], "FULL_MARKET")
        self.assertEqual(pack["opportunity_inputs"]["candidates"], [])

    def test_missing_account_degrades_analysis_but_blocks_exact_action(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory),
                {"request_id": "r6", "requested_at_beijing": "2026-09-21T14:00:00+08:00"},
                {"market_date": "2026-09-21", "latest_snapshot": "snap.json"},
                {"status": "MISSING", "positions": []},
                {"rules_version": "V2.2.31"},
                {"decision_freshness": {"post_request": True}, "quotes": []},
                formal_discovery={"status": "PASS", "candidates": []},
            )
        self.assertTrue(pack["formal_analysis_availability"]["available"])
        self.assertEqual(pack["formal_analysis_availability"]["status"], "DEGRADED")
        self.assertIn("ACCOUNT_FACT_NOT_READY_EXACT_AMOUNT_SHARE_BLOCKED", pack["formal_analysis_availability"]["limitations"])
        self.assertFalse(pack["formal_action_readiness"]["ready"])
        self.assertIn("ACCOUNT_FACT_NOT_READY", pack["formal_action_readiness"]["blockers"])

    def test_pre_request_fallback_degrades_analysis_but_does_not_qualify_action(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory),
                {"request_id": "r7", "requested_at_beijing": "2026-09-21T14:00:00+08:00"},
                {"market_date": "2026-09-21", "latest_snapshot": "snap.json"},
                {"status": "VALID", "positions": []},
                {"rules_version": "V2.2.31"},
                {"decision_freshness": {"post_request": False, "fallback_allowed": True}, "quotes": []},
                formal_discovery={"status": "PASS", "candidates": []},
            )
        self.assertTrue(pack["formal_analysis_availability"]["available"])
        self.assertEqual(pack["formal_analysis_availability"]["status"], "DEGRADED")
        self.assertIn("REQUEST_SCOPED_PIT_NOT_RESOLVED_FALLBACK_AVAILABLE", pack["formal_analysis_availability"]["limitations"])
        self.assertFalse(pack["formal_action_readiness"]["ready"])
        self.assertIn("REQUEST_SCOPED_PIT_NOT_RESOLVED", pack["formal_action_readiness"]["blockers"])

    def test_missing_request_identity_remains_global_packet_block(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory), {},
                {"market_date": "2026-09-21", "latest_snapshot": "snap.json"},
                {"status": "VALID", "positions": []},
                {"rules_version": "V2.2.31"},
                {"decision_freshness": {"post_request": True}, "quotes": []},
                formal_discovery={"status": "PASS", "candidates": []},
            )
        self.assertFalse(pack["formal_analysis_availability"]["available"])
        self.assertEqual(pack["formal_analysis_availability"]["status"], "BLOCKED")
        self.assertIn("REQUEST_IDENTITY_MISSING", pack["formal_analysis_availability"]["global_blockers"])
        self.assertFalse(pack["formal_action_readiness"]["ready"])

    def test_missing_latency_fields_remain_explicit(self):
        result = query_context.build_fast_path_latency(
            {"requested_at_beijing": "2026-09-05T10:00:00+08:00"},
            {"captured_at": "2026-09-05T10:00:02+08:00"},
            {"generated_at": "2026-09-05T10:00:03+08:00"},
            {"refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH", "decision_freshness": {"post_request": True}},
            "2026-09-05T10:00:04+08:00",
        )
        self.assertIsNone(result["refresh_start_latency"])
        self.assertEqual(result["total_fast_path_latency"], 4.0)
        self.assertEqual(result["t_new_current"], "2026-09-05T10:00:02+08:00")

    def test_latency_durations_are_non_negative_and_observed(self):
        result = query_context.build_fast_path_latency(
            {"requested_at_beijing": "2026-09-05T10:00:00+08:00", "refresh_started_at_beijing": "2026-09-05T10:00:01+08:00"},
            {"captured_at": "2026-09-05T10:00:03+08:00"},
            {"generated_at": "2026-09-05T10:00:04+08:00"},
            {"refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH", "decision_freshness": {"post_request": True}},
            "2026-09-05T10:00:05+08:00",
        )
        self.assertEqual(result["refresh_start_latency"], 1.0)
        self.assertEqual(result["refresh_duration"], 2.0)
        self.assertEqual(result["post_current_decision_latency"], 1.0)
        self.assertEqual(result["total_fast_path_latency"], 5.0)


    def test_user_intraday_trace_metadata_is_non_authoritative_and_request_scoped(self):
        result = query_context.build_fast_path_latency(
            {
                "request_id": "user-1",
                "requested_at_beijing": "2026-09-14T11:06:00+08:00",
                "screenshot_account_fact_available_at_beijing": "2026-09-14T11:06:02+08:00",
                "refresh_request_id": "refresh-1",
                "minimum_legal_inputs_ready_at_beijing": "2026-09-14T11:06:08+08:00",
                "final_answer_identity": "answer-1",
                "required_account_persistence_identity": "account-sync-1",
            },
            {"captured_at": "2026-09-14T11:06:05+08:00"},
            {"generated_at": "2026-09-14T11:06:08+08:00"},
            {"refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH", "decision_freshness": {"post_request": True}},
            "2026-09-14T11:06:10+08:00",
        )
        self.assertEqual(result["user_request_received"], "2026-09-14T11:06:00+08:00")
        self.assertEqual(result["screenshot_account_fact_available"], "2026-09-14T11:06:02+08:00")
        self.assertEqual(result["market_refresh_identity"], "refresh-1")
        self.assertEqual(result["final_answer_identity"], "answer-1")
        self.assertEqual(result["required_account_persistence_identity"], "account-sync-1")
        self.assertTrue(result["trace_metadata_is_non_authoritative"])


if __name__ == "__main__":
    unittest.main()
