from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MinuteShadowComplexityContractTest(unittest.TestCase):
    def test_shadow_is_validation_on_change_not_scheduled_production(self):
        workflow = (ROOT / ".github/workflows/minute-path-shadow.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("push:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("cron:", workflow)
        for script in (
            "scripts/build_minute_path_features.py",
            "scripts/probe_index_minute_acceptance.py",
            "scripts/probe_stock_minute_production.py",
            "scripts/validate_minute_context_integration.py",
        ):
            self.assertIn(script, workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)


if __name__ == "__main__":
    unittest.main()
