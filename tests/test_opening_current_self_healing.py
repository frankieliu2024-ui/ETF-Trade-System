from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import runtime_self_heal  # noqa: E402


class OpeningCurrentSelfHealingTests(unittest.TestCase):
    def _assess(self, *, current: dict, now: datetime, closed_dates: list[str] | None = None) -> dict:
        values = {
            "config/runtime_policy.json": {
                "self_healing": {
                    "enabled": True,
                    "watchdog_trigger_age_seconds": 780,
                    "minimum_retrigger_seconds": 480,
                    "max_snapshot_repair_attempts_per_hour": 2,
                }
            },
            "config/market/a_share_trading_calendar_2026.json": {
                "closed_dates": closed_dates or [],
            },
            "data/state/CURRENT.json": current,
            "data/state/runtime_health.json": {
                "status": "PASS",
                "quality_status": "PASS",
            },
            "data/state/system_consistency.json": {"status": "PASS"},
            "data/state/query_context.json": {},
            "data/state/decision_context.json": {},
            "data/state/self_healing_status.json": {},
        }

        def fake_load(path: Path, default=None):
            normalized = path.as_posix()
            for key, value in values.items():
                if normalized.endswith(key):
                    return value
            return default

        with (
            mock.patch.object(runtime_self_heal, "load_json", side_effect=fake_load),
            mock.patch.object(runtime_self_heal, "master_version", return_value="V2.2.31"),
        ):
            return runtime_self_heal.assess(now)

    def test_previous_day_current_is_an_explicit_opening_recovery(self):
        status = self._assess(
            current={
                "market_date": "2026-08-31",
                "captured_at": "2026-08-31T15:01:10+08:00",
                "latest_valid_node": "close",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-01T09:27:00+08:00"),
        )
        self.assertTrue(status["same_day_current_required"])
        self.assertTrue(status["same_day_current_missing"])
        self.assertEqual(status["classification"], "SAME_DAY_CURRENT_MISSING")
        self.assertEqual(status["recommended_action"], "REFRESH_SNAPSHOT")

    def test_same_day_current_remains_on_existing_age_contract(self):
        status = self._assess(
            current={
                "market_date": "2026-09-01",
                "captured_at": "2026-09-01T09:25:00+08:00",
                "latest_valid_node": "auction",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-01T09:27:00+08:00"),
        )
        self.assertFalse(status["same_day_current_missing"])
        self.assertEqual(status["classification"], "HEALTHY")
        self.assertEqual(status["recommended_action"], "NONE")

    def test_expected_decision_pulse_missing_triggers_recovery_even_when_age_is_under_threshold(self):
        status = self._assess(
            current={
                "market_date": "2026-09-01",
                "captured_at": "2026-09-01T10:15:00+08:00",
                "latest_valid_node": "1010",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-01T10:24:00+08:00"),
        )
        self.assertEqual(status["expected_pulse_at"], "2026-09-01T10:20:00+08:00")
        self.assertTrue(status["expected_pulse_missing"])
        self.assertEqual(status["classification"], "EXPECTED_PULSE_MISSING")
        self.assertEqual(status["recommended_action"], "REFRESH_SNAPSHOT")

    def test_late_watchdog_still_evaluates_the_due_decision_pulse(self):
        for minute in ("10:25", "10:29", "11:25", "13:25", "14:25"):
            with self.subTest(minute=minute):
                hour, value = minute.split(":")
                now = datetime.fromisoformat(f"2026-09-01T{minute}:00+08:00")
                pulse = f"2026-09-01T{hour}:20:00+08:00"
                status = self._assess(
                    current={
                        "market_date": "2026-09-01",
                        "captured_at": "2026-09-01T{0}:15:00+08:00".format(hour),
                        "latest_valid_node": "late",
                        "node_status": "READY",
                        "rules_version": "V2.2.31",
                    },
                    now=now,
                )
                self.assertEqual(status["expected_pulse_at"], pulse)
                self.assertTrue(status["expected_pulse_missing"])
                self.assertEqual(status["classification"], "EXPECTED_PULSE_MISSING")

    def test_after_due_window_existing_age_contract_remains_authoritative(self):
        status = self._assess(
            current={
                "market_date": "2026-09-01",
                "captured_at": "2026-09-01T10:15:00+08:00",
                "latest_valid_node": "1015",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-01T10:30:00+08:00"),
        )
        self.assertIsNone(status["expected_pulse_at"])

    def test_expected_decision_pulse_is_healthy_when_current_has_arrived(self):
        status = self._assess(
            current={
                "market_date": "2026-09-01",
                "captured_at": "2026-09-01T10:20:00+08:00",
                "latest_valid_node": "1020",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-01T10:24:00+08:00"),
        )
        self.assertFalse(status["expected_pulse_missing"])
        self.assertEqual(status["recommended_action"], "NONE")

    def test_all_formal_decision_watchdog_checkpoints_use_their_prior_pulse(self):
        for checkpoint, pulse in (("10:24", "10:20"), ("11:24", "11:20"), ("13:24", "13:20"), ("14:24", "14:20")):
            with self.subTest(checkpoint=checkpoint):
                now = datetime.fromisoformat(f"2026-09-01T{checkpoint}:00+08:00")
                status = self._assess(
                    current={
                        "market_date": "2026-09-01",
                        "captured_at": f"2026-09-01T{pulse}:00+08:00",
                        "latest_valid_node": pulse.replace(":", ""),
                        "node_status": "READY",
                        "rules_version": "V2.2.31",
                    },
                    now=now,
                )
                self.assertEqual(status["expected_pulse_at"], f"2026-09-01T{pulse}:00+08:00")
                self.assertFalse(status["expected_pulse_missing"])

    def test_expected_pulse_recovery_escalates_at_hourly_attempt_limit(self):
        current = {
            "market_date": "2026-09-01",
            "captured_at": "2026-09-01T10:15:00+08:00",
            "latest_valid_node": "1010",
            "node_status": "READY",
            "rules_version": "V2.2.31",
        }
        now = datetime.fromisoformat("2026-09-01T10:24:00+08:00")
        with mock.patch.object(runtime_self_heal, "load_json") as load:
            def fake_load(path, default=None):
                if str(path).endswith("runtime_policy.json"):
                    return {"self_healing": {"enabled": True, "watchdog_trigger_age_seconds": 780, "minimum_retrigger_seconds": 480, "max_snapshot_repair_attempts_per_hour": 2}}
                if str(path).endswith("a_share_trading_calendar_2026.json"):
                    return {"closed_dates": []}
                if str(path).endswith("CURRENT.json"):
                    return current
                if str(path).endswith("runtime_health.json"):
                    return {"status": "PASS"}
                if str(path).endswith("system_consistency.json"):
                    return {"status": "PASS"}
                if str(path).endswith("self_healing_status.json"):
                    return {"snapshot_refresh_attempts": ["2026-09-01T10:00:00+08:00", "2026-09-01T10:10:00+08:00"]}
                return {}
            load.side_effect = fake_load
            with mock.patch.object(runtime_self_heal, "master_version", return_value="V2.2.31"):
                status = runtime_self_heal.assess(now)
        self.assertEqual(status["classification"], "PERSISTENT_RUNTIME_FAILURE")
        self.assertEqual(status["recommended_action"], "ESCALATE")

    def test_midday_and_auction_contracts_are_not_reinterpreted_as_decision_pulses(self):
        auction = self._assess(
            current={
                "market_date": "2026-09-01",
                "captured_at": "2026-09-01T09:25:00+08:00",
                "latest_valid_node": "auction",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-01T09:25:00+08:00"),
        )
        midday = self._assess(
            current={
                "market_date": "2026-09-01",
                "captured_at": "2026-09-01T11:30:00+08:00",
                "latest_valid_node": "close",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-01T12:25:00+08:00"),
        )
        self.assertIsNone(auction["expected_pulse_at"])
        self.assertIsNone(midday["expected_pulse_at"])

    def test_expected_pulse_recovery_honors_cooldown_and_attempt_limit(self):
        current = {
            "market_date": "2026-09-01",
            "captured_at": "2026-09-01T10:15:00+08:00",
            "latest_valid_node": "1010",
            "node_status": "READY",
            "rules_version": "V2.2.31",
        }
        now = datetime.fromisoformat("2026-09-01T10:24:00+08:00")
        with mock.patch.object(runtime_self_heal, "load_json") as load:
            def fake_load(path, default=None):
                if str(path).endswith("runtime_policy.json"):
                    return {"self_healing": {"enabled": True, "watchdog_trigger_age_seconds": 780, "minimum_retrigger_seconds": 480, "max_snapshot_repair_attempts_per_hour": 2}}
                if str(path).endswith("a_share_trading_calendar_2026.json"):
                    return {"closed_dates": []}
                if str(path).endswith("CURRENT.json"):
                    return current
                if str(path).endswith("runtime_health.json"):
                    return {"status": "PASS"}
                if str(path).endswith("system_consistency.json"):
                    return {"status": "PASS"}
                if str(path).endswith("self_healing_status.json"):
                    return {"last_snapshot_refresh_trigger_at": "2026-09-01T10:20:00+08:00"}
                return {}
            load.side_effect = fake_load
            with mock.patch.object(runtime_self_heal, "master_version", return_value="V2.2.31"):
                status = runtime_self_heal.assess(now)
        self.assertEqual(status["classification"], "REPAIR_COOLDOWN")

    def test_watchdog_deduplicates_inflight_market_snapshot_dispatch(self):
        workflow = (ROOT / ".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
        dispatch = workflow.split("- name: Dispatch replacement market snapshot", 1)[1]
        self.assertIn("gh run list --workflow market-snapshot.yml", dispatch)
        self.assertIn('status == "queued" or .status == "in_progress"', dispatch)

    def test_event_wake_sources_are_existing_cross_market_workflows(self):
        workflow = (ROOT / ".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
        trigger = workflow.split("  workflow_run:", 1)[1].split("  push:", 1)[0]
        expected = {
            "ETF market snapshot",
            "ETF opening-auction CURRENT fallback",
            "Overseas pre-open pulse",
            "US extended-hours pulse",
            "ETF system consistency",
        }
        self.assertEqual(
            set(line.strip().strip('"') for line in trigger.splitlines() if line.strip().startswith('- "')),
            expected,
        )
        self.assertIn("types: [completed]", trigger)
        assess = workflow.split("- name: Assess deterministic runtime health", 1)[1]
        self.assertTrue(assess.index("python scripts/runtime_self_heal.py --assess") < assess.index("- name: Assess dedicated cross-market pulse heartbeats"))
        self.assertNotIn("if: ${{ github.event_name == 'workflow_run' }}", assess.split("- name: Assess dedicated cross-market pulse heartbeats", 1)[0])

    def test_event_wake_recovers_after_primary_and_scheduled_watchdog_are_absent(self):
        absent_schedules = {
            "ETF market snapshot": "ABSENT",
            "ETF runtime self-healing watchdog": "ABSENT",
        }
        self.assertEqual(set(absent_schedules.values()), {"ABSENT"})
        for wake_source in ("Overseas pre-open pulse", "ETF system consistency"):
            with self.subTest(wake_source=wake_source):
                status = self._assess(
                    current={
                        "market_date": "2026-09-01",
                        "captured_at": "2026-09-01T10:15:00+08:00",
                        "latest_valid_node": "1015",
                        "node_status": "READY",
                        "rules_version": "V2.2.31",
                    },
                    now=datetime.fromisoformat("2026-09-01T10:25:00+08:00"),
                )
                self.assertEqual(status["classification"], "EXPECTED_PULSE_MISSING")
                self.assertEqual(status["recommended_action"], "REFRESH_SNAPSHOT")

        workflow = (ROOT / ".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
        dispatch = workflow.split("- name: Dispatch replacement market snapshot", 1)[1]
        self.assertEqual(dispatch.count("gh workflow run market-snapshot.yml --ref main"), 1)
        self.assertEqual(dispatch.count("python scripts/runtime_self_heal.py --record-trigger"), 1)

    def test_event_wake_preserves_single_dispatch_for_queued_or_in_progress_snapshot(self):
        workflow = (ROOT / ".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
        dispatch = workflow.split("- name: Dispatch replacement market snapshot", 1)[1]
        self.assertIn('active=$(gh run list --workflow market-snapshot.yml --limit 5 --json status', dispatch)
        self.assertIn('select(.status == "queued" or .status == "in_progress")', dispatch)
        self.assertIn('if [ "$active" != "0" ]; then', dispatch)
        self.assertIn("no duplicate dispatch", dispatch)
        self.assertEqual(dispatch.count("gh workflow run market-snapshot.yml --ref main"), 1)

    def test_closed_day_does_not_trigger_opening_recovery(self):
        status = self._assess(
            current={
                "market_date": "2026-08-31",
                "captured_at": "2026-08-31T15:01:10+08:00",
                "latest_valid_node": "close",
                "node_status": "READY",
                "rules_version": "V2.2.31",
            },
            now=datetime.fromisoformat("2026-09-05T09:27:00+08:00"),
        )
        self.assertFalse(status["same_day_current_required"])
        self.assertFalse(status["same_day_current_missing"])
        self.assertNotEqual(status["classification"], "SAME_DAY_CURRENT_MISSING")

    def test_opening_recovery_keeps_canonical_producer_and_safety_boundary(self):
        workflow = (ROOT / ".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
        snapshot = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        fallback = (ROOT / ".github/workflows/opening-auction-current-fallback.yml").read_text(encoding="utf-8")
        self.assertIn('steps.assess.outputs.action == \'REFRESH_SNAPSHOT\'', workflow)
        self.assertIn("gh workflow run market-snapshot.yml --ref main", workflow)
        self.assertIn("Publish core snapshot and CURRENT immediately", snapshot)
        self.assertIn("etf-market-snapshot-${{ github.ref }}", snapshot)
        self.assertIn("etf-market-snapshot-${{ github.ref }}", fallback)
        self.assertIn("newer_current_already_exists", (ROOT / "scripts/cloud_runner_snapshot.py").read_text(encoding="utf-8"))
        self.assertNotIn("may_auto_trade", workflow)

    def test_assessment_does_not_claim_recovery_without_watchdog_execution(self):
        current = {
            "market_date": "2026-08-31",
            "captured_at": "2026-08-31T15:01:10+08:00",
            "latest_valid_node": "close",
            "node_status": "READY",
            "rules_version": "V2.2.31",
        }
        with mock.patch.object(runtime_self_heal, "atomic_write_json") as write:
            status = self._assess(current=current, now=datetime.fromisoformat("2026-09-01T09:27:00+08:00"))
        write.assert_not_called()
        self.assertEqual(status["recommended_action"], "REFRESH_SNAPSHOT")
        self.assertNotIn("recovered", status["reason"].lower())

if __name__ == "__main__":
    unittest.main()
