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
            },
            now=datetime.fromisoformat("2026-09-01T09:27:00+08:00"),
        )
        self.assertFalse(status["same_day_current_missing"])
        self.assertEqual(status["classification"], "HEALTHY")
        self.assertEqual(status["recommended_action"], "NONE")

    def test_closed_day_does_not_trigger_opening_recovery(self):
        status = self._assess(
            current={
                "market_date": "2026-08-31",
                "captured_at": "2026-08-31T15:01:10+08:00",
                "latest_valid_node": "close",
                "node_status": "READY",
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
        }
        with mock.patch.object(runtime_self_heal, "atomic_write_json") as write:
            status = self._assess(current=current, now=datetime.fromisoformat("2026-09-01T09:27:00+08:00"))
        write.assert_not_called()
        self.assertEqual(status["recommended_action"], "REFRESH_SNAPSHOT")
        self.assertNotIn("recovered", status["reason"].lower())

if __name__ == "__main__":
    unittest.main()
