from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
            {"status": "VALID", "positions": []},
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
        self.assertFalse(trace["minimum_legal_inputs_ready_is_stop_boundary"])
        self.assertEqual(trace["canonical_chain_status"], "FORMAL_COMPLETION_PENDING")
        self.assertEqual(trace["screenshot_account_fact_available_at"], "2026-09-14T10:00:05+08:00")
        self.assertEqual(trace["market_refresh_identity"], "query-refresh-1")
        self.assertEqual(trace["first_qualified_market_fact_identity"], "000001.SH")
        self.assertEqual(trace["first_qualified_market_fact_as_of"], "2026-09-14T10:00:18+08:00")
        self.assertEqual(trace["minimum_legal_inputs_ready_at"], "2026-09-14T10:00:30+08:00")
        self.assertEqual(trace["required_account_persistence_identity"], "requests/live_snapshot/manual-20260914-1.json")
        self.assertTrue(trace["trace_metadata_is_non_authoritative"])
        self.assertFalse(trace["trace_metadata_is_decision_gate"])
        self.assertFalse(trace["business_semantics_changed"])

    def test_downstream_completion_cannot_masquerade_as_original_decision_latency(self):
        trace = build_query_context.build_fast_path_latency(
            {
                "request_id": "decision-1__formal_completion",
                "parent_request_id": "decision-1",
                "request_type": "STATE_SYNC_ONLY",
                "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
                "requested_at_beijing": "2026-09-23T18:01:40+08:00",
            },
            {"captured_at": "2026-09-23T15:27:38+08:00"},
            {"status": "VALID", "positions": []},
            {"generated_at_beijing": "2026-09-23T18:26:43+08:00"},
            {"decision_freshness": {}, "quotes": []},
            "2026-09-23T18:26:43+08:00",
        )
        self.assertEqual(trace["latency_observation_role"], "DOWNSTREAM_COMPLETION_NOT_DECISION_REQUEST")
        self.assertEqual(trace["original_decision_request_identity"], "decision-1")
        self.assertFalse(trace["may_measure_original_decision_latency"])
        self.assertIsNone(trace["request_to_core_result_latency"])
        self.assertIsNone(trace["canonical_ingress_to_decision_latency"])
        self.assertEqual(trace["latency_status"], "DOWNSTREAM_NOT_DECISION_LATENCY")

    def test_formal_decision_request_keeps_request_scoped_latency(self):
        trace = build_query_context.build_fast_path_latency(
            {"request_id": "decision-2", "source": "CHATGPT_USER_INTERACTION", "requested_at_beijing": "2026-09-23T14:03:00+08:00"},
            {"captured_at": "2026-09-23T14:03:01+08:00"},
            {"status": "VALID", "positions": []},
            {"generated_at_beijing": "2026-09-23T14:04:03+08:00"},
            {"decision_freshness": {"post_request": True}, "quotes": []},
            "2026-09-23T14:04:03+08:00",
        )
        self.assertEqual(trace["latency_observation_role"], "FORMAL_DECISION_REQUEST")
        self.assertEqual(trace["original_decision_request_identity"], "decision-2")
        self.assertTrue(trace["may_measure_original_decision_latency"])
        self.assertEqual(trace["request_to_core_result_latency"], 63.0)
        self.assertEqual(trace["latency_status"], "OBSERVED")

    def test_utc_request_timestamp_is_converted_without_inference(self):
        trace = build_query_context.build_fast_path_latency(
            {"request_id": "utc-1", "requested_at_utc": "2026-09-21T01:50:52.737Z"},
            {},
            {},
            {},
            {"decision_freshness": {}, "quotes": []},
            "",
        )
        self.assertEqual(trace["manual_request_received_at"], "2026-09-21T09:50:52+08:00")
        self.assertEqual(trace["t0"], "2026-09-21T09:50:52+08:00")

    def test_request_file_without_request_id_gets_stable_diagnostic_identity(self):
        request = {
            "requested_at_beijing": "2026-09-15T11:21:00+08:00",
            "scenario": "INTRADAY",
            "intent": "FORMAL_INTRADAY_ANALYSIS",
            "source": "CHATGPT_USER_REQUEST_WITH_BROKER_SCREENSHOT",
            "account_fact_note": "ordinary broker screenshot; no new trade quantities observed",
            "_request_file": "requests/live_snapshot/20260915_1121_manual_intraday_chat.json",
        }

        first = build_query_context.build_fast_path_latency(
            request,
            {"captured_at": "2026-09-15T11:21:20+08:00"},
            {"status": "VALID", "positions": []},
            {"generated_at_beijing": "2026-09-15T11:21:30+08:00"},
            {"decision_freshness": {"post_request": True}, "quotes": []},
            "2026-09-15T11:21:40+08:00",
        )
        second = build_query_context.build_fast_path_latency(
            dict(request),
            {"captured_at": "2026-09-15T11:21:20+08:00"},
            {"status": "VALID", "positions": []},
            {"generated_at_beijing": "2026-09-15T11:21:30+08:00"},
            {"decision_freshness": {"post_request": True}, "quotes": []},
            "2026-09-15T11:21:40+08:00",
        )

        self.assertNotEqual(first["manual_request_identity"], "UNKNOWN")
        self.assertEqual(first["manual_request_identity"], second["manual_request_identity"])
        self.assertTrue(first["manual_request_identity"].startswith("20260915_1121_manual_intraday_chat-"))
        self.assertEqual(first["manual_request_received_at"], "2026-09-15T11:21:00+08:00")
        self.assertFalse(first["minimum_legal_inputs_ready_is_stop_boundary"])
        self.assertTrue(first["trace_metadata_is_non_authoritative"])
        self.assertFalse(first["trace_metadata_is_decision_gate"])
        self.assertFalse(first["business_semantics_changed"])

    def test_unobservable_product_phases_remain_unknown_not_inferred(self):
        trace = build_query_context.build_fast_path_latency(
            {},
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

    def test_build_with_request_file_carries_existing_path_into_trace_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "requests" / "live_snapshot" / "20260915_1121_manual_intraday_chat.json"
            request_path.parent.mkdir(parents=True)
            request_path.write_text(
                '{"requested_at_beijing":"2026-09-15T11:21:00+08:00","source":"CHATGPT_USER_REQUEST_WITH_BROKER_SCREENSHOT"}',
                encoding="utf-8",
            )
            (root / "config" / "market").mkdir(parents=True)
            (root / "config" / "runtime_policy.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "config" / "runtime_policy.json").write_text("{}", encoding="utf-8")
            (root / "data" / "state").mkdir(parents=True)

            with patch.object(build_query_context, "build_market_quote_context", return_value={"decision_freshness": {"post_request": True}, "quotes": []}), \
                 patch.object(build_query_context, "read_current", return_value={"captured_at": "2026-09-15T11:21:10+08:00"}), \
                 patch.object(build_query_context, "read_account_fact", return_value={"status": "VALID"}), \
                 patch.object(build_query_context, "build_decision_context", return_value={"generated_at_beijing": "2026-09-15T11:21:20+08:00"}):
                context = build_query_context.build(
                    root,
                    request_file="requests/live_snapshot/20260915_1121_manual_intraday_chat.json",
                )

        trace = context["fast_path_latency"]
        self.assertNotEqual(trace["manual_request_identity"], "UNKNOWN")
        self.assertEqual(trace["manual_request_received_at"], "2026-09-15T11:21:00+08:00")

    def test_minimum_ready_precedes_full_decision_context_when_required_facts_are_present(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "stock_market_context.json").write_text(
                '{"objects":{"300750":{"quality_status":"PASS","as_of_beijing":"2026-09-18T14:51:06+08:00"}}}',
                encoding="utf-8",
            )
            trace = build_query_context.build_fast_path_latency(
                {"request_id":"r1","requested_at_beijing":"2026-09-18T14:50:00+08:00"},
                {"captured_at":"2026-09-18T14:50:20+08:00"},
                {"status":"VALID","positions":[{"asset_type":"STOCK","code":"300750","quantity":200}]},
                {"generated_at_beijing":"2026-09-18T14:55:04+08:00"},
                {"decision_freshness":{"post_request":True},"quotes":[{"object_code":"000001","data_time_beijing":"2026-09-18T14:50:16+08:00","quality_status":"PASS"}]},
                "2026-09-18T14:51:07+08:00",
                root,
            )
            self.assertEqual(trace["minimum_legal_inputs_ready_at"], "2026-09-18T14:51:07+08:00")
            self.assertEqual(trace["t_decision_ready"], "2026-09-18T14:55:04+08:00")
            self.assertLess(trace["minimum_legal_inputs_ready_at"], trace["t_decision_ready"])

    def test_missing_held_stock_market_fact_keeps_minimum_ready_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "stock_market_context.json").write_text('{"objects":{}}', encoding="utf-8")
            trace = build_query_context.build_fast_path_latency(
                {"request_id":"r2","requested_at_beijing":"2026-09-18T14:50:00+08:00"},
                {"captured_at":"2026-09-18T14:50:20+08:00"},
                {"status":"VALID","positions":[{"asset_type":"STOCK","code":"300750","quantity":200}]},
                {"generated_at_beijing":"2026-09-18T14:55:04+08:00"},
                {"decision_freshness":{"post_request":True},"quotes":[{"object_code":"000001","data_time_beijing":"2026-09-18T14:50:16+08:00","quality_status":"PASS"}]},
                "2026-09-18T14:51:07+08:00",
                root,
            )
            self.assertEqual(trace["minimum_legal_inputs_ready_at"], "UNKNOWN")

    def test_build_exposes_measured_in_process_latency_without_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "requests" / "live_snapshot" / "decision-latency.json"
            request_path.parent.mkdir(parents=True)
            request_path.write_text(
                '{"request_id":"decision-latency","requested_at_beijing":"2026-09-25T06:34:00+08:00","source":"CHATGPT_USER_INTERACTION","intent":"FORMAL_INTRADAY_ANALYSIS"}',
                encoding="utf-8",
            )
            (root / "config" / "market").mkdir(parents=True)
            (root / "config" / "runtime_policy.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "config" / "runtime_policy.json").write_text("{}", encoding="utf-8")
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "runtime_health.json").write_text(
                '{"run_started_at":"2026-09-25T06:39:50+08:00","captured_at_beijing":"2026-09-25T06:40:10+08:00","workflow_run_id":"123"}',
                encoding="utf-8",
            )

            with patch.object(build_query_context, "build_market_quote_context", return_value={"decision_freshness": {"post_request": True}, "quotes": []}), \
                 patch.object(build_query_context, "read_current", return_value={"captured_at": "2026-09-25T06:40:10+08:00"}), \
                 patch.object(build_query_context, "read_account_fact", return_value={"status": "VALID", "positions": []}), \
                 patch.object(build_query_context, "build_decision_context", return_value={"generated_at_beijing": "2026-09-25T06:40:44+08:00"}):
                context = build_query_context.build(
                    root,
                    request_file="requests/live_snapshot/decision-latency.json",
                )

        trace = context["fast_path_latency"]
        stages = trace["in_process_stage_durations_seconds"]
        self.assertIsNotNone(stages["freshness_assurance_and_quote_context"])
        self.assertIsNotNone(stages["decision_context_build"])
        self.assertIsNotNone(stages["decision_fact_pack_build"])
        self.assertIsNotNone(stages["query_context_build_total"])
        self.assertEqual(trace["workflow_runtime_observation"]["run_started_at_beijing"], "2026-09-25T06:39:50+08:00")
        self.assertEqual(trace["workflow_runtime_observation"]["core_snapshot_finished_at_beijing"], "2026-09-25T06:40:10+08:00")
        self.assertEqual(trace["workflow_runtime_observation"]["workflow_run_id"], "123")
        self.assertIn("UNKNOWN remains explicit", trace["waterfall_rule"])

    def test_query_context_step_precedes_full_derived_context_steps(self):
        workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        query_pos = workflow.index("- name: Build on-demand query context")
        e2e_pos = workflow.index("- name: Build E2E usability state")
        state_pos = workflow.index("- name: Build state context and candidates")
        review_pos = workflow.index("- name: Build post-market review context")
        self.assertLess(query_pos, e2e_pos)
        self.assertLess(query_pos, state_pos)
        self.assertLess(query_pos, review_pos)


    def test_market_snapshot_workflow_preserves_triggering_request_path_after_reset(self):
        workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("triggering_request_file=", workflow)
        self.assertIn("TRIGGERING_REQUEST_FILE=", workflow)
        self.assertIn('REQUEST_FILE="${TRIGGERING_REQUEST_FILE:-}"', workflow)
        self.assertIn('python scripts/build_query_context.py --force-refresh --symbols "$REQUEST_SYMBOLS" "${REQUEST_ARGS[@]}"', workflow)

    def test_formal_completion_stays_on_state_sync_fast_path(self):
        workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn('echo "formal_completion=$formal_completion" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn('steps.state_sync.outputs.formal_completion != \'true\'', workflow)

        query_block = workflow.split("- name: Build on-demand query context", 1)[1].split("- name: Build E2E usability state", 1)[0]
        e2e_block = workflow.split("- name: Build E2E usability state", 1)[1].split("- name: Build state context and candidates", 1)[0]
        state_block = workflow.split("- name: Build state context and candidates", 1)[1].split("- name: Build post-market review context", 1)[0]
        review_block = workflow.split("- name: Build post-market review context", 1)[1].split("- name: Commit downstream runtime/data artifacts", 1)[0]

        for block in (query_block, e2e_block, state_block, review_block):
            self.assertIn("steps.state_sync.outputs.formal_completion != 'true'", block)

    def test_post_request_snapshot_does_not_force_discovery_rescan(self):
        workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        branch = workflow.split('elif [ "${{ steps.snapshot_result.outputs.snapshot_written }}" = "true" ] && [ "$REQUEST_REQUIRES_REFRESH" = "true" ]; then', 1)[1]
        branch = branch.split('elif [ "${{ steps.snapshot_result.outputs.snapshot_written }}" = "true" ]; then', 1)[0]
        self.assertIn('python scripts/build_query_context.py --run-discovery "${REQUEST_ARGS[@]}"', branch)
        self.assertNotIn('python scripts/build_query_context.py --force-refresh "${REQUEST_ARGS[@]}"', branch)


if __name__ == "__main__":
    unittest.main()
