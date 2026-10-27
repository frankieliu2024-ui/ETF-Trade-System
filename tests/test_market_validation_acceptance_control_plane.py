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
    classify_path,
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

    def test_acceptance_does_not_persist_diagnostic_only_maintenance_snapshot(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("data/state/maintenance_diagnostic.json", workflow)

    def test_acceptance_does_not_recreate_unused_last_known_good_state(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        maintenance = (ROOT / "scripts" / "maintenance_guard.py").read_text(encoding="utf-8")
        self.assertNotIn("last_known_good.json", workflow)
        self.assertNotIn("last_known_good.json", maintenance)
        self.assertNotIn('"last_known_good"', maintenance)

    def test_acceptance_rebuilds_execution_quality_once_via_state_context(self):
        source = ACCEPTANCE.read_text(encoding="utf-8")
        self.assertNotIn('scripts/build_execution_quality.py', source)
        self.assertEqual(source.count('scripts/build_state_context.py'), 1)
        self.assertIn("quality_rc=state_rc", source)

    def test_acceptance_reuses_canonical_checks_and_has_no_recursive_push(self):
        source = ACCEPTANCE.read_text(encoding="utf-8")
        self.assertIn("check_system_consistency.py", source)
        self.assertIn("maintenance_guard.py", source)
        self.assertIn("build_e2e_status.py", source)
        self.assertIn("recursive_push_required", source)
        self.assertNotIn("git push origin", source)
        self.assertNotIn("workflow_dispatch:", source)

    def test_consistency_workflow_uses_ephemeral_artifact_report(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- name: Run full system consistency check", source)
        self.assertIn("ETF_CONSISTENCY_REPORT_PATH", source)
        self.assertIn("actions/upload-artifact@v7", source)
        self.assertIn("system-consistency-", source)
        self.assertIn("system_consistency.json", source)
        self.assertNotIn("production_acceptance:", source)

    def test_consistency_workflow_does_not_stage_current_state(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("git add data/state/CURRENT.json", source)
        self.assertNotIn("git add -A", source)
        self.assertIn("git add data/state/system_consistency.json", source)
        self.assertIn("git push origin HEAD:main", source)

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

    def test_acceptance_scope_decodes_git_quoted_utf8_paths(self):
        quoted = '"ETF\\345\\275\\223\\345\\211\\215\\347\\212\\266\\346\\200\\201_DASHBOARD.md"'
        formal = classify_paths([quoted])
        self.assertTrue(formal["required"])
        self.assertEqual(formal["classes"], [FORMAL_PROJECTION_MUTATION])
        self.assertEqual(formal["paths"], ["ETF当前状态_DASHBOARD.md"])

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

    def test_full_acceptance_classes_have_natural_workflow_coverage(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        trigger_block = source.split("  schedule:", 1)[0]
        representatives = {
            "ETF规则_MASTER.md",
            "ETF_SYSTEM_INDEX.md",
            "ETF当前状态_DASHBOARD.md",
            "ETF市场行情档案_2026.md",
            "ETF交易复盘与经验库_2026.md",
            "docs/**",
            "events/**",
            "requests/**",
            "tests/**",
        }
        for path in representatives:
            self.assertTrue(classify_paths([path])["required"], path)
        for pattern in (
            '      - "ETF规则_MASTER.md"',
            '      - "ETF_SYSTEM_INDEX.md"',
            '      - "ETF当前状态_DASHBOARD.md"',
            '      - "ETF市场行情档案_2026.md"',
            '      - "ETF交易复盘与经验库_2026.md"',
            '      - "docs/**"',
            '      - "events/**"',
            '      - "requests/**"',
            '      - "tests/**"',
        ):
            self.assertIn(pattern, trigger_block)





if __name__ == "__main__":
    unittest.main()
