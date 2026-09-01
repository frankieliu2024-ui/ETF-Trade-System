from pathlib import Path
import unittest

class DelayedCloseRecoveryTests(unittest.TestCase):
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
