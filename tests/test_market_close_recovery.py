from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import unittest
from unittest import mock


SHANGHAI = timezone(timedelta(hours=8))


class DelayedCloseRecoveryTests(unittest.TestCase):
    def test_real_schedule_at_1459_becomes_close_boundary_intent(self):
        from scripts.runtime_session_gate import scheduled_close_boundary_intent

        dt = datetime(2026, 9, 2, 14, 59, 4, tzinfo=SHANGHAI)
        self.assertTrue(
            scheduled_close_boundary_intent(
                dt, "schedule", "*/10 2-3,5-6 * * 1-5"
            )
        )

    def test_schedule_before_boundary_does_not_wait_or_relabel_live(self):
        from scripts.runtime_session_gate import scheduled_close_boundary_intent

        dt = datetime(2026, 9, 2, 14, 50, tzinfo=SHANGHAI)
        self.assertFalse(
            scheduled_close_boundary_intent(
                dt, "schedule", "*/10 2-3,5-6 * * 1-5"
            )
        )

    def test_dispatch_cannot_create_close_intent(self):
        from scripts.runtime_session_gate import scheduled_close_boundary_intent

        dt = datetime(2026, 9, 2, 14, 59, tzinfo=SHANGHAI)
        self.assertFalse(
            scheduled_close_boundary_intent(
                dt, "workflow_dispatch", "*/10 2-3,5-6 * * 1-5"
            )
        )

    def test_delayed_exact_close_schedule_still_requires_real_boundary(self):
        from scripts.cloud_runner_snapshot import scheduled_close_recovery_intent

        dt = datetime(2026, 9, 2, 14, 59, tzinfo=SHANGHAI)
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_EVENT_NAME": "schedule",
                "SCHEDULED_CRON": "0,10 7 * * 1-5",
                "SCHEDULED_CLOSE_INTENT": "true",
            },
        ):
            self.assertFalse(scheduled_close_recovery_intent(dt, "2026-09-02"))

        dt = datetime(2026, 9, 2, 15, 0, 4, tzinfo=SHANGHAI)
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_EVENT_NAME": "schedule",
                "SCHEDULED_CRON": "*/10 2-3,5-6 * * 1-5",
                "SCHEDULED_CLOSE_INTENT": "true",
            },
        ):
            self.assertTrue(scheduled_close_recovery_intent(dt, "2026-09-02"))

    def test_schedule_intent_contract_is_explicit(self):
        text = (
            Path(__file__).parents[1] / "scripts/runtime_session_gate.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SCHEDULED_CRON", text)
        self.assertIn("scheduled_close_boundary_intent", text)
        self.assertIn("wait_for_close_boundary_seconds", text)

    def test_effective_and_observed_times_are_separate(self):
        text = (
            Path(__file__).parents[1] / "scripts/cloud_runner_snapshot.py"
        ).read_text(encoding="utf-8")
        self.assertIn("effective_market_time_beijing", text)
        self.assertIn("provider_observed_at_beijing", text)


if __name__ == "__main__":
    unittest.main()
