from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from formal_file_mutation_gateway import (  # noqa: E402
    ALLOWED_FORMAL_FACT_FILES,
    replace_managed_block,
    resolve_formal_fact_path,
    upsert_managed_line,
    write_formal_text_if_changed,
)


class FormalFileMutationGatewayTests(unittest.TestCase):
    def test_master_is_forbidden(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PermissionError):
                resolve_formal_fact_path(Path(td), "ETF规则_MASTER.md")

    def test_only_three_formal_fact_files_are_allowed(self):
        self.assertEqual(
            ALLOWED_FORMAL_FACT_FILES,
            {
                "ETF当前状态_DASHBOARD.md",
                "ETF市场行情档案_2026.md",
                "ETF交易复盘与经验库_2026.md",
            },
        )
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PermissionError):
                resolve_formal_fact_path(Path(td), "data/state/CURRENT.json")

    def test_managed_block_replacement_preserves_outside_text(self):
        original = "# T\npre\n<!-- S -->\nold\n<!-- E -->\npost\n"
        updated = replace_managed_block(original, "<!-- S -->", "<!-- E -->", "new")
        self.assertEqual(updated, "# T\npre\n<!-- S -->\nnew\n<!-- E -->\npost\n")

    def test_upsert_is_idempotent_by_key(self):
        base = "# T\n<!-- S -->\nK｜old\nX｜stay\n<!-- E -->\n"
        once = upsert_managed_line(base, "<!-- S -->", "<!-- E -->", "K", "new")
        twice = upsert_managed_line(once, "<!-- S -->", "<!-- E -->", "K", "new")
        self.assertEqual(once, twice)
        self.assertIn("K｜new", twice)
        self.assertNotIn("K｜old", twice)
        self.assertIn("X｜stay", twice)

    def test_atomic_write_reports_change_only_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "ETF当前状态_DASHBOARD.md"
            (root / name).write_text("old\n", encoding="utf-8")
            self.assertTrue(write_formal_text_if_changed(root, name, "new\n"))
            self.assertFalse(write_formal_text_if_changed(root, name, "new\n"))
            self.assertEqual((root / name).read_text(encoding="utf-8"), "new\n")


    def test_metadata_follows_real_fact_changes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "ETF交易复盘与经验库_2026.md"
            old = "> 更新时点：2026-08-27（说明）\\n2026-08-28｜既有事实\\n"
            (root / name).write_text(old, encoding="utf-8")
            changed = old.replace("2026-08-28｜既有事实", "2026-08-31｜正式review")
            self.assertTrue(write_formal_text_if_changed(root, name, changed))
            result = (root / name).read_text(encoding="utf-8")
            self.assertIn("更新时点：2026-08-31", result)
            self.assertFalse(write_formal_text_if_changed(root, name, result))

    def test_metadata_does_not_change_without_content_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "ETF市场行情档案_2026.md"
            text = "> 更新时点：2026-08-27（说明）\\n2026-08-28｜事实\\n"
            (root / name).write_text(text, encoding="utf-8")
            self.assertFalse(write_formal_text_if_changed(root, name, text))
            self.assertEqual((root / name).read_text(encoding="utf-8"), text)

    def test_crlf_write_preserves_newline_style(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "ETF市场行情档案_2026.md"
            (root / name).write_bytes(
                "> 更新时点：2026-08-27\\r\\n前置\\r\\n".encode("utf-8")
            )
            write_formal_text_if_changed(root, name, "> 更新时点：2026-08-31\\n前置\\n2026-08-31｜事实\\n")
            raw = (root / name).read_bytes()
            self.assertIn(b"\\r\\n", raw)
            self.assertNotIn(b"\\r\\r\\n", raw)


if __name__ == "__main__":
    unittest.main()
