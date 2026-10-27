from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "us-extended-hours-pulse.yml"


class UsPulseScheduleBackstopTests(unittest.TestCase):
    def test_single_unified_fixed_pulse_schedule_uses_one_canonical_workflow(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "5,15,25,35,45,55 8-23 * * 1-5"', text)
        self.assertIn('cron: "5,15,25,35,45,55 0-1 * * 2-6"', text)
        self.assertNotIn('2,22,42', text)
        self.assertIn("group: etf-main-state-writer", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertEqual(text.count("name: US extended-hours pulse"), 1)


if __name__ == "__main__":
    unittest.main()
