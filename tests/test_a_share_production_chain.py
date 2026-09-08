from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_account_stock_market as stock_market  # noqa: E402
import runtime_session_gate  # noqa: E402
import build_stock_context as stock_context  # noqa: E402
from scheduled_pulse_slot import resolve_scheduled_pulse  # noqa: E402


class AShareProductionChainTests(unittest.TestCase):
    def test_stock_classifier_reads_canonical_etf_universe(self):
        codes = stock_context.load_etf_codes()
        configured = {
            str(item["code"])
            for item in json.loads((ROOT / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))["objects"]
        }
        self.assertEqual(codes, configured)
        self.assertEqual(len(codes), 13)
        self.assertTrue({"515220", "513350"}.issubset(codes))

    def test_observation_additions_use_verified_direct_provider_contract(self):
        universe = json.loads((ROOT / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))
        by_code = {str(item["code"]): item for item in universe["objects"]}
        self.assertEqual(by_code["515220"]["thscode"], "515220.SH")
        self.assertEqual(by_code["513350"]["thscode"], "513350.SH")
        priority = json.loads((ROOT / "config/market/provider_priority.json").read_text(encoding="utf-8"))
        for thscode in ("515220.SH", "513350.SH"):
            self.assertEqual(priority["objects"][thscode], ["tencent_qq", "hithink_finance", "eastmoney_push2"])
            self.assertTrue(priority["object_fallback_policy"][thscode]["direct_only"])

    def test_dynamic_account_stocks_use_supported_market_snapshot(self):
        stocks = [
            {"code": "300750", "name": "宁德时代", "quantity": 100, "market_value": 1},
            {"code": "601138", "name": "工业富联", "quantity": 500, "market_value": 2},
        ]

        def fake_run(command, **kwargs):
            self.assertEqual(command[1:3], ["market", "snapshot"])
            self.assertIn("300750.SZ,601138.SH", command)
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({
                "ok": True,
                "data": {
                    "item": [
                        {"thscode": "300750.SZ", "open_price": 10, "high_price": 12, "low_price": 9, "last_price": 11, "prev_price": 10, "price_change_ratio_pct": 10, "volume": 1, "turnover": 2},
                        {"thscode": "601138.SH", "open_price": 20, "high_price": 21, "low_price": 18, "last_price": 19, "prev_price": 20, "price_change_ratio_pct": -5, "volume": 3, "turnover": 4},
                    ],
                    "timestamp": 1787542230000,
                },
                "meta": {"source": "remote", "request_id": "test"},
            }), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(stock_market.subprocess, "run", side_effect=fake_run):
            result = stock_market.fetch_many("hithink-finance", stocks)

        self.assertEqual(set(result), {"300750", "601138"})
        self.assertTrue(all(item["quality_status"] == "PASS" for item in result.values()))
        self.assertTrue(all(item["as_of_beijing"].endswith("+08:00") for item in result.values()))

    def test_shared_slot_contract_covers_apac_primary_and_backstop_without_extra_frequency(self):
        primary = resolve_scheduled_pulse("*/10 0-7 * * 1-5", datetime.fromisoformat("2026-09-08T14:05:00+08:00"))
        backstop = resolve_scheduled_pulse("2,22,42 0-7 * * 1-5", datetime.fromisoformat("2026-09-08T14:05:00+08:00"))
        self.assertEqual(primary["latest_candidate_slot_at"], "2026-09-08T14:00:00+08:00")
        self.assertEqual(backstop["latest_candidate_slot_at"], "2026-09-08T14:02:00+08:00")
        self.assertEqual(primary["schedule_delay_class"], "AMBIGUOUS")
        self.assertEqual(backstop["schedule_delay_class"], "AMBIGUOUS")
        self.assertEqual(primary["natural_pulse_identity"], "slot:2026-09-08T14:00:00+08:00")
        self.assertEqual(backstop["natural_pulse_identity"], "slot:2026-09-08T14:02:00+08:00")
        workflow = (ROOT / ".github/workflows/overseas-preopen-pulse.yml").read_text(encoding="utf-8")
        self.assertIn("id: pulse_gate", workflow)
        self.assertIn("steps.pulse_gate.outputs.eligible == 'true'", workflow)
        self.assertIn("group: etf-overseas-preopen-pulse", workflow)

    def test_shared_slot_contract_rejects_severely_delayed_apac_primary_and_backstop(self):
        primary = resolve_scheduled_pulse("*/10 0-7 * * 1-5", datetime.fromisoformat("2026-09-08T16:55:00+08:00"))
        backstop = resolve_scheduled_pulse("2,22,42 0-7 * * 1-5", datetime.fromisoformat("2026-09-08T16:55:00+08:00"))
        self.assertEqual(primary["schedule_delay_class"], "AMBIGUOUS")
        self.assertTrue(primary["eligible"])
        self.assertEqual(backstop["schedule_delay_class"], "AMBIGUOUS")
        self.assertTrue(backstop["eligible"])

    def test_shared_slot_contract_keeps_non_schedule_ingress_eligible(self):
        event = resolve_scheduled_pulse("", datetime.fromisoformat("2026-09-08T14:05:00+08:00"))
        self.assertEqual(event["schedule_delay_class"], "NOT_SCHEDULED")
        self.assertTrue(event["eligible"])
        self.assertIsNone(event["natural_pulse_identity"])
    def test_schedule_identity_rejects_severely_delayed_old_cron_event(self):
        now = datetime.fromisoformat("2026-09-08T13:55:42+08:00")
        observation = runtime_session_gate.resolve_scheduled_pulse("15,20,25,30,40,50 1 * * 1-5", now)
        self.assertEqual(observation["latest_candidate_slot_at"], "2026-09-08T09:50:00+08:00")
        self.assertEqual(observation["schedule_delay_class"], "AMBIGUOUS")
        self.assertGreaterEqual(observation["candidate_delay_seconds"], 4 * 60 * 60)
        workflow = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("SCHEDULED_CRON:", workflow)
        self.assertIn("9,19,29,39,49,59 2-3,5-6 * * 1-5", workflow)
        self.assertNotIn("*/10 2-3,5-6 * * 1-5", workflow)
        self.assertIn("stale_scheduled_pulse", (ROOT / "scripts/runtime_session_gate.py").read_text(encoding="utf-8"))
        self.assertFalse(observation["natural_pulse_eligible"])

    def test_shifted_natural_pulse_preserves_decision_freshness_margin(self):
        now = datetime.fromisoformat("2026-09-08T13:25:00+08:00")
        observation = runtime_session_gate.scheduled_cron_observability("9,19,29,39,49,59 5-6 * * 1-5", now)
        self.assertEqual(observation["latest_candidate_slot_at"], "2026-09-08T13:19:00+08:00")
        self.assertEqual(observation["schedule_delay_class"], "AMBIGUOUS")
        self.assertLessEqual(observation["candidate_delay_seconds"], 6 * 60)
        self.assertIn("2026-09-08T13:19:00+08:00", observation["natural_pulse_identity"])

    def test_live_snapshot_request_classifier_preserves_single_workflow_semantics(self):
        refresh = {"request_type": "MARKET_QUOTE_REFRESH", "force_refresh": True}
        formal_only = {"formal_decision": {"decision_id": "d1"}}
        account_only = {"source": "CHATGPT_USER_BROKER_SCREENSHOT", "interaction_scenario": "BROKER_SCREENSHOT_SYNC", "account_fact": {}}
        trade_only = {"trade_event": {"event_id": "t1"}}
        review_only = {"formal_review": {"reviewed_at_beijing": "2026-09-08T15:39:00+08:00"}}
        hybrid = {"formal_decision": {"decision_id": "d1"}, "wait_for_refresh": True, "require_post_request_snapshot": True}

        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(refresh), "REFRESH_BEARING")
        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(formal_only), "STATE_SYNC_ONLY")
        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(account_only), "STATE_SYNC_ONLY")
        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(trade_only), "STATE_SYNC_ONLY")
        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(review_only), "STATE_SYNC_ONLY")
        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(hybrid), "HYBRID")

    def test_1325_two_stage_request_does_not_recapture_on_formal_persistence(self):
        first_refresh = {"query_intent": "EXPLICIT_LATEST", "wait_for_refresh": True, "require_post_request_snapshot": True}
        second_formal = {"formal_decision": {"decision_id": "20260908_1325"}}
        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(first_refresh), "REFRESH_BEARING")
        self.assertEqual(runtime_session_gate.classify_live_snapshot_request(second_formal), "STATE_SYNC_ONLY")
        workflow = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("steps.session_gate.outputs.request_class != 'STATE_SYNC_ONLY'", workflow)
        self.assertIn("classify_live_snapshot_request", workflow)
        self.assertIn("python scripts/process_state_sync_request.py", workflow)
        self.assertIn("select_point_in_time_snapshot", (ROOT / "scripts/process_state_sync_request.py").read_text(encoding="utf-8"))

    def test_scheduled_and_query_requests_share_canonical_snapshot_producer(self):
        production = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        on_demand = (ROOT / ".github/workflows/on-demand-market-data.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", production)
        self.assertIn("if: ${{ steps.session_gate.outputs.should_capture == 'true' }}", production)
        self.assertIn("github.event_name != 'push'", production)
        self.assertIn("steps.snapshot_result.outputs.snapshot_written", production)
        self.assertIn("git add -A -- data/market/snapshots data/state", production)
        self.assertNotIn("run_full_snapshot_recovery.py", on_demand)
        self.assertNotIn("data/state/CURRENT.json", on_demand)
        self.assertFalse((ROOT / ".github/workflows/full-snapshot-recovery.yml").exists())

    def test_core_snapshot_does_not_eagerly_resolve_optional_hithink_cli(self):
        snapshot = (ROOT / "scripts/cloud_runner_snapshot.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("Start optional Hithink fallback CLI bootstrap", workflow)
        self.assertIn("HITHINK_CLI_INSTALL_PID", workflow)
        self.assertIn("Tencent is primary; resolve Hithink only if", snapshot)
        self.assertGreaterEqual(snapshot.count("cli = None"), 2)
        self.assertIn("cli = cli or cli_path()", snapshot)

    def test_scheduled_formal_decision_wait_contract_is_separate_from_interactive_budget(self):
        policy = json.loads((ROOT / "config/runtime_policy.json").read_text(encoding="utf-8"))
        scheduled = policy["scheduled_formal_decision"]
        interactive = policy["interactive_decision_freshness"]
        self.assertEqual(interactive["short_wait_budget_seconds"], 20)
        self.assertEqual(scheduled["wait_mode"], "UNTIL_POST_REQUEST_CURRENT_OR_TERMINAL_REFRESH_FAILURE")
        self.assertGreater(scheduled["max_wait_seconds"], interactive["short_wait_budget_seconds"])
        self.assertTrue(scheduled["reuse_inflight_refresh"])
        self.assertTrue(scheduled["require_final_post_request_recheck"])

    def test_shared_slot_contract_covers_us_primary_and_backstop_without_extra_frequency(self):
        primary = resolve_scheduled_pulse("*/10 8-23 * * 1-5", datetime.fromisoformat("2026-09-08T17:05:00+08:00"))
        backstop = resolve_scheduled_pulse("2,22,42 8-23 * * 1-5", datetime.fromisoformat("2026-09-08T17:05:00+08:00"))
        self.assertEqual(primary["latest_candidate_slot_at"], "2026-09-08T17:00:00+08:00")
        self.assertEqual(backstop["latest_candidate_slot_at"], "2026-09-08T17:02:00+08:00")
        self.assertEqual(primary["schedule_delay_class"], "BOUNDED_DELAY")
        self.assertEqual(backstop["schedule_delay_class"], "BOUNDED_DELAY")
        self.assertTrue(primary["eligible"])
        self.assertTrue(backstop["eligible"])
        workflow = (ROOT / ".github/workflows/us-extended-hours-pulse.yml").read_text(encoding="utf-8")
        self.assertIn("id: pulse_gate", workflow)
        self.assertIn("steps.pulse_gate.outputs.eligible == 'true'", workflow)
        self.assertIn("us_pulse_runtime_health.json", workflow)

    def test_shared_slot_contract_rejects_severely_delayed_us_backstop_and_old_cron(self):
        primary = resolve_scheduled_pulse("*/10 8-23 * * 1-5", datetime.fromisoformat("2026-09-08T17:05:00+08:00"))
        backstop = resolve_scheduled_pulse("2,22,42 8-23 * * 1-5", datetime.fromisoformat("2026-09-08T20:05:00+08:00"))
        old_cron = resolve_scheduled_pulse("15,20,25,30,40,50 1 * * 1-5", datetime.fromisoformat("2026-09-08T13:55:42+08:00"))
        self.assertEqual(primary["schedule_delay_class"], "BOUNDED_DELAY")
        self.assertTrue(primary["eligible"])
        self.assertEqual(backstop["schedule_delay_class"], "AMBIGUOUS")
        self.assertFalse(backstop["eligible"])
        self.assertEqual(old_cron["schedule_delay_class"], "AMBIGUOUS")
        self.assertTrue(old_cron["eligible"])
        self.assertIn("SCHEDULED_CRON", (ROOT / ".github/workflows/us-extended-hours-pulse.yml").read_text(encoding="utf-8"))
        self.assertFalse(old_cron["natural_pulse_eligible"])

    def test_us_non_schedule_ingress_remains_eligible(self):
        event = resolve_scheduled_pulse("", datetime.fromisoformat("2026-09-08T17:05:00+08:00"))
        self.assertEqual(event["schedule_delay_class"], "NOT_SCHEDULED")
        self.assertTrue(event["eligible"])

    def test_repeated_cron_without_occurrence_identity_is_ambiguous_in_all_three_domains(self):
        cases = (
            ("A", "9,19,29,39,49,59 5-6 * * 1-5", datetime.fromisoformat("2026-09-08T14:25:00+08:00")),
            ("APAC", "*/10 0-7 * * 1-5", datetime.fromisoformat("2026-09-08T14:05:00+08:00")),
            ("US", "*/10 8-23 * * 1-5", datetime.fromisoformat("2026-09-08T17:05:00+08:00")),
        )
        for domain, cron, now in cases:
            with self.subTest(domain=domain):
                result = resolve_scheduled_pulse(cron, now)
                self.assertEqual(result["schedule_delay_class"], "AMBIGUOUS")
                self.assertTrue(result["refresh_eligible"])
                self.assertTrue(result["eligible"])
                self.assertFalse(result["natural_pulse_eligible"])
                self.assertEqual(result["natural_pulse_identity_status"], "AMBIGUOUS")
                self.assertIsNone(result["natural_pulse_identity"])
                self.assertIsNone(result["scheduled_slot_at"])
                self.assertIsNotNone(result["latest_candidate_slot_at"])


if __name__ == "__main__":
    unittest.main()

