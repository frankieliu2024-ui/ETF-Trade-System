from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.acceptance_scope import (
    FORMAL_RULE_OR_CONFIG,
    STABLE_CODE_OR_WORKFLOW_CHANGE,
    classify_paths,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "生产变更与并发写入协议_V1.0.md"
MIRROR = ROOT / "config" / "maintenance" / "production_mutation_protocol.json"
WORKFLOW = ROOT / ".github" / "workflows" / "system-consistency.yml"


class ProductionAdmissionConcurrencyContractTests(unittest.TestCase):
    def test_protocol_keeps_three_admission_outcomes_and_separates_work_from_integration(self):
        source = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("准入只允许三个结果", source)
        for outcome in ("EXECUTE_NOW", "OBSERVE", "DO_NOT_CHANGE"):
            self.assertIn(outcome, source)
        self.assertIn("四维正交判定：准入、工作、集成与优先级", source)
        self.assertIn("INTEGRATION=WAIT_FOR_PREDECESSOR", source)
        self.assertIn("这不是 `OBSERVE`", source)
        self.assertIn("串行约束适用于共享 main 的正式写入", source)
        self.assertIn("前序事项的真实暴露或集成等待不得改变后续独立事项的Admission", source)
        self.assertIn("ADMISSION=EXECUTE_NOW", source)
        self.assertIn("INTEGRATION=WAIT_FOR_PREDECESSOR", source)
        self.assertNotIn("下一项独立生产语义才保持", source)

    def test_machine_mirror_records_the_same_orthogonal_semantics(self):
        mirror = json.loads(MIRROR.read_text(encoding="utf-8"))
        admission = mirror["change_admission"]
        self.assertEqual(admission["decision_outcomes"], ["EXECUTE_NOW", "OBSERVE", "DO_NOT_CHANGE"])
        self.assertIn("work_concurrency", admission)
        self.assertIn("production_integration", admission)
        self.assertIn("priority_semantics", admission)
        self.assertIn("does not convert the admission to OBSERVE", admission["work_concurrency"])
        self.assertIn("not a fourth admission outcome", admission["production_integration"])

    def test_independent_work_remains_full_acceptance_without_new_admission(self):
        self.assertEqual(
            classify_paths(["scripts/independent_fix.py"])["classes"],
            [STABLE_CODE_OR_WORKFLOW_CHANGE],
        )
        self.assertTrue(classify_paths(["scripts/independent_fix.py"])["required"])
        self.assertEqual(
            classify_paths(["config/maintenance/production_mutation_protocol.json"])["classes"],
            [FORMAL_RULE_OR_CONFIG],
        )
        self.assertEqual(
            json.loads(MIRROR.read_text(encoding="utf-8"))["change_admission"]["decision_outcomes"],
            ["EXECUTE_NOW", "OBSERVE", "DO_NOT_CHANGE"],
        )

    def test_workflow_has_natural_pr_and_main_trigger_coverage(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pull_request:", source)
        self.assertIn("push:", source)
        self.assertIn('      - "tests/**"', source)
        self.assertIn('      - "docs/**"', source)
        self.assertIn('      - "scripts/**"', source)


if __name__ == "__main__":
    unittest.main()
