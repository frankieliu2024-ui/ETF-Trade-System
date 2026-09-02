from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "system-consistency.yml"
VALIDATOR = ROOT / "scripts" / "check_system_consistency.py"
ACCEPTANCE = ROOT / "scripts" / "run_production_acceptance.py"


class ValidationAcceptanceControlPlaneTest(unittest.TestCase):
    def test_validator_supports_ephemeral_report_without_persisting(self):
        source = VALIDATOR.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("--no-persist", source)
        self.assertIn("--report-path", source)
        self.assertTrue(any(isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "parse_args" for node in ast.walk(tree)))

    def test_acceptance_reuses_canonical_checks_and_has_no_recursive_push(self):
        source = ACCEPTANCE.read_text(encoding="utf-8")
        self.assertIn("check_system_consistency.py", source)
        self.assertIn("maintenance_guard.py", source)
        self.assertIn("build_e2e_status.py", source)
        self.assertIn("recursive_push_required", source)
        self.assertNotIn("git push origin", source)
        self.assertNotIn("workflow_dispatch:", source)

    def test_consistency_workflow_separates_validation_and_acceptance(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--no-persist --report-path", source)
        self.assertIn("production_acceptance:", source)
        self.assertIn("ETF_CONSISTENCY_REPORT_PATH", source)
        self.assertIn("diff-tree --no-commit-id --name-only -m -r", source)
        self.assertIn("run_production_acceptance.py", source)
        self.assertNotIn("Refresh formal overseas and Asia index context", source)
        self.assertNotIn("Run requested research historical backfill", source)
        self.assertNotIn("Apply confirmed trade fact correction", source)
        self.assertNotIn("Refresh decision and query context", source)
        self.assertNotIn('      - "data/state/CURRENT.json"', source)

    def test_workflow_does_not_add_a_second_state_store(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(source.count("system_consistency.json"), 5)
        self.assertEqual(source.count("maintenance_health.json"), 2)
        self.assertEqual(source.count("e2e_status.json"), 3)


if __name__ == "__main__":
    unittest.main()
