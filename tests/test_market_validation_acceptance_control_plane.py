from __future__ import annotations

import ast
from pathlib import Path
import unittest

from scripts.acceptance_scope import (
    ACCOUNT_FACT_MUTATION,
    DERIVED_STATE_ONLY,
    FORMAL_PROJECTION_MUTATION,
    STABLE_CODE_OR_WORKFLOW_CHANGE,
    UNKNOWN,
    classify_paths,
)
from scripts.build_e2e_status import maintenance_component


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

    def test_acceptance_scope_is_class_based(self):
        formal = classify_paths(["ETF当前状态_DASHBOARD.md"])
        self.assertTrue(formal["required"])
        self.assertEqual(formal["classes"], [FORMAL_PROJECTION_MUTATION])

        pulse = classify_paths(["data/state/CURRENT.json", "data/market/snapshots/latest.json"])
        self.assertFalse(pulse["required"])
        self.assertIn("ORDINARY_MARKET_PULSE", pulse["classes"])

        stable = classify_paths(["scripts/build_e2e_status.py"])
        self.assertTrue(stable["required"])
        self.assertEqual(stable["classes"], [STABLE_CODE_OR_WORKFLOW_CHANGE])

        account = classify_paths(["data/state/account_fact.json"])
        self.assertEqual(account["classes"], [ACCOUNT_FACT_MUTATION])

        derived = classify_paths(["data/state/query_context.json"])
        self.assertFalse(derived["required"])
        self.assertEqual(derived["classes"], [DERIVED_STATE_ONLY])

        unknown = classify_paths(["new/canonical_fact.json"])
        self.assertTrue(unknown["required"])
        self.assertEqual(unknown["classes"], [UNKNOWN])

    def test_maintenance_only_block_does_not_block_e2e(self):
        self.assertEqual(
            maintenance_component({"status": "FAIL", "system_consistency_status": "WARNING",
                                    "system_consistency": {"hard_error_count": 0},
                                    "reconciliation": {"status": "PASS"}})["status"],
            "DEGRADED",
        )
        self.assertEqual(
            maintenance_component({"status": "FAIL", "system_consistency_status": "FAIL",
                                    "system_consistency": {"hard_error_count": 1},
                                    "reconciliation": {"status": "PASS"}})["status"],
            "BLOCKED",
        )
        self.assertEqual(
            maintenance_component({"status": "FAIL", "system_consistency_status": "WARNING",
                                    "system_consistency": {"hard_error_count": 0},
                                    "reconciliation": {"status": "FAIL"}})["status"],
            "BLOCKED",
        )

    def test_workflow_does_not_add_a_second_state_store(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(source.count("system_consistency.json"), 6)
        self.assertEqual(source.count("maintenance_health.json"), 2)
        self.assertEqual(source.count("e2e_status.json"), 3)


if __name__ == "__main__":
    unittest.main()
