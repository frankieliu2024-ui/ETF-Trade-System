import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SystemReadinessPreflightTests(unittest.TestCase):
    def test_preflight_is_read_only_and_passes(self):
        completed = subprocess.run(
            [sys.executable, "scripts/run_system_readiness_preflight.py", "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["production_notification_sent"])
        self.assertEqual(result["failed_checks"], [])

    def test_preflight_has_no_production_mutation_calls(self):
        text = (ROOT / "scripts/run_system_readiness_preflight.py").read_text(encoding="utf-8")
        self.assertNotIn("workflow_dispatch", text)
        self.assertNotIn("PUSHPLUS_TOKEN", text)
        self.assertIn("read_only", text)
        self.assertIn("isolated_replay", text)


if __name__ == "__main__":
    unittest.main()
