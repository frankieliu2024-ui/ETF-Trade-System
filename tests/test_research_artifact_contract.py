import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.research_artifact_contract import (
    ARCHIVE_START,
    archive_completed_research,
    load_artifact,
    validate_completed_artifact,
    validate_report_against_artifact,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "research/backtests/etf_t_swing_stage1_validation.json"
REPORT = ROOT / "research/reports/ETF反复做T与波段专项研究.md"


class ResearchArtifactContractTest(unittest.TestCase):
    def test_current_stage1_is_complete_and_provenanced(self):
        self.assertEqual(validate_completed_artifact(ARTIFACT), [])
        self.assertEqual(len(load_artifact(ARTIFACT)["current_11"]), 11)

    def test_empty_artifact_fails_completed_contract(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty.json"
            path.write_text("{}", encoding="utf-8")
            self.assertTrue(validate_completed_artifact(path))

    def test_missing_semantic_fields_fail(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "partial.json"
            path.write_text(json.dumps({
                "artifact_kind": "COMPLETED_RESEARCH_VALIDATION",
                "completion_status": "COMPLETED",
                "research_id": "x",
            }), encoding="utf-8")
            errors = validate_completed_artifact(path)
            self.assertIn("missing qualification", errors)
            self.assertIn("missing current_11", errors)

    def test_report_headlines_are_checked_against_artifact(self):
        artifact = load_artifact(ARTIFACT)
        self.assertEqual(validate_report_against_artifact(REPORT.read_text(encoding="utf-8"), artifact), [])
        altered = REPORT.read_text(encoding="utf-8").replace("-60.55pp", "-60.54pp")
        self.assertTrue(validate_report_against_artifact(altered, artifact))

    def test_archive_is_idempotent_and_uses_formal_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "research/backtests").mkdir(parents=True)
            shutil.copyfile(ARTIFACT, root / "research/backtests/stage1.json")
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text("# 经验库\n\n## 3. 历史研究\n\n## 4. OBS观察\n", encoding="utf-8")
            master = root / "ETF规则_MASTER.md"
            master.write_text("MASTER\n", encoding="utf-8")
            self.assertTrue(archive_completed_research(root))
            first = experience.read_text(encoding="utf-8")
            self.assertIn(ARCHIVE_START, first)
            self.assertIn("etf_t_swing_stage1", first)
            self.assertFalse(archive_completed_research(root))
            self.assertEqual(first, experience.read_text(encoding="utf-8"))
            self.assertEqual(master.read_text(encoding="utf-8"), "MASTER\n")


if __name__ == "__main__":
    unittest.main()
