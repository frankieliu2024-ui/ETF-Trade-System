import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPERIENCE = ROOT / "ETF交易复盘与经验库_2026.md"


class CaseHumanOutputCleanupTests(unittest.TestCase):
    def test_current_case_block_is_human_only(self):
        text = EXPERIENCE.read_text(encoding="utf-8")
        self.assertNotIn("已归入CASE", text)
        self.assertNotIn("待复盘CASE", text)
        self.assertNotIn("CASE目录与映射", text)
        self.assertNotIn("CASE详细记录", text)
        self.assertIn("<!-- AUTO_CASE_INTAKE_START -->\n<!-- AUTO_CASE_INTAKE_END -->", text)
        self.assertEqual(len(re.findall(r"^### 2\\.16 CASE-20260902-01", text, re.MULTILINE)), 1)
        self.assertEqual(len(re.findall(r"^### 2\\.17 CASE-20260903-01", text, re.MULTILINE)), 1)

    def test_case_objects_are_isolated(self):
        text = EXPERIENCE.read_text(encoding="utf-8")
        start = text.index("### 2.16 CASE-20260902-01")
        end = text.index("<!-- AUTO_CASE_DETAILS_END -->", start)
        block = text[start:end]
        self.assertIn("159326", block)
        self.assertNotIn("518880", block)
        self.assertNotIn("515880", block)

        start = text.index("### 2.17 CASE-20260903-01")
        block = text[start:end]
        self.assertIn("518880", block)
        self.assertNotIn("159326", block)
        self.assertNotIn("515880", block)

    def test_writer_has_legacy_row_purge(self):
        source = (ROOT / "scripts/process_state_sync_request.py").read_text(encoding="utf-8")
        self.assertIn("def _purge_case_mapping_rows", source)
        self.assertIn("_purge_case_mapping_rows()", source)


if __name__ == "__main__":
    unittest.main()
