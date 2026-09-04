from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "us-extended-hours-pulse.yml"


class UsPulseScheduleBackstopTests(unittest.TestCase):
    def test_primary_and_staggered_backstop_share_one_canonical_workflow(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "*/10 8-23 * * 1-5"', text)
        self.assertIn('cron: "*/10 0-1 * * 2-6"', text)
        self.assertIn('cron: "2,22,42 8-23 * * 1-5"', text)
        self.assertIn('cron: "2,22,42 0-1 * * 2-6"', text)
        self.assertIn("group: etf-us-extended-hours-pulse", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertEqual(text.count("name: US extended-hours pulse"), 1)


if __name__ == "__main__":
    unittest.main()
