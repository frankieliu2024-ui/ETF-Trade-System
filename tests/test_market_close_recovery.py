from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import unittest
from unittest import mock

class DelayedCloseRecoveryTests(unittest.TestCase):
    def test_delayed_schedule_intent_bypasses_runner_time_window_only_for_close_cron(self):
        from scripts.cloud_runner_snapshot import scheduled_close_recovery_intent
        dt = datetime(2026, 9, 1, 20, 29, tzinfo=timezone(timedelta(hours=8)))
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule", "SCHEDULED_CRON": "0,10 7 * * 1-5"}):
            self.assertTrue(scheduled_close_recovery_intent(dt, "2026-09-01"))
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule", "SCHEDULED_CRON": "*/10 2-3,5-6 * * 1-5"}):
            self.assertFalse(scheduled_close_recovery_intent(dt, "2026-09-01"))

    def test_schedule_intent_contract_is_explicit(self):
        text = (Path(__file__).parents[1] / "scripts/runtime_session_gate.py").read_text(encoding="utf-8")
        self.assertIn("SCHEDULED_CRON", text)
        self.assertIn("delayed_scheduled_close_recovery", text)
    def test_effective_and_observed_times_are_separate(self):
        text = (Path(__file__).parents[1] / "scripts/cloud_runner_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("effective_market_time_beijing", text)
        self.assertIn("provider_observed_at_beijing", text)

if __name__ == "__main__":
    unittest.main()
