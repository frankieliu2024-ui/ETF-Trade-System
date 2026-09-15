from __future__ import annotations

import json
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
    normalize_human_readable_projection,
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

    def test_case_heading_is_not_prefixed_and_replaces_malformed_entry(self):
        base = "# T\n<!-- S -->\n2026-09-02｜### CASE-20260902-01：old\nX｜stay\n<!-- E -->\n"
        once = upsert_managed_line(
            base,
            "<!-- S -->",
            "<!-- E -->",
            "2026-09-02",
            "### CASE-20260902-01：new",
        )
        twice = upsert_managed_line(
            once,
            "<!-- S -->",
            "<!-- E -->",
            "2026-09-02",
            "### CASE-20260902-01：new",
        )
        self.assertEqual(once, twice)
        self.assertIn("### CASE-20260902-01：new", twice)
        self.assertNotIn("2026-09-02｜### CASE-20260902-01", twice)
        self.assertIn("X｜stay", twice)

    def test_atomic_write_reports_change_only_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "ETF当前状态_DASHBOARD.md"
            (root / name).write_text("old\n", encoding="utf-8")
            self.assertTrue(write_formal_text_if_changed(root, name, "new\n"))
            self.assertFalse(write_formal_text_if_changed(root, name, "new\n"))
            self.assertEqual((root / name).read_text(encoding="utf-8"), "new\n")

    def test_dashboard_projection_keeps_missing_pnl_unknown_and_hides_machine_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data/state").mkdir(parents=True)
            (root / "data/state/account_fact.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "name": "缺失盈亏ETF",
                                "code": "111111",
                                "quantity": 100,
                                "cost": 1.0,
                                "last_price": 1.1,
                                "market_value": 110.0,
                            },
                            {
                                "name": "真实零盈亏ETF",
                                "code": "222222",
                                "quantity": 100,
                                "cost": 1.0,
                                "last_price": 1.0,
                                "market_value": 100.0,
                                "pnl": 0.0,
                                "pnl_pct": 0.0,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            text = (
                "# D\n> 场景：ACCOUNT_FACT_MAINTENANCE\n"
                "|标的|数量|成本|现价|市值|浮动盈亏|\n"
                "|-|-:|-:|-:|-:|-:|\n"
                "|缺失盈亏ETF（111111）|100|1.000|1.100|110.00元|0.00元（+0.00%）|\n"
                "|真实零盈亏ETF（222222）|100|1.000|1.000|100.00元|0.00元（+0.00%）|\n"
                "- 风险许可：NO_NEW_FORMAL_MORNING_DECISION_REGISTERED\n"
                "- 生命周期：\n"
                "- 缺失盈亏ETF（111111）：事实不足\n"
                "- 唯一主候选：NONE_REGISTERED\n"
                "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->\n"
                "FEE_old｜机器纠错行\n"
                "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->\n"
            )
            normalized = normalize_human_readable_projection(root, "ETF当前状态_DASHBOARD.md", text)
            self.assertIn("|缺失盈亏ETF（111111）|100|1.000|1.100|110.00元|—|", normalized)
            self.assertIn("|真实零盈亏ETF（222222）|100|1.000|1.000|100.00元|0.00元（+0.00%）|", normalized)
            self.assertIn("场景：账户事实维护", normalized)
            self.assertIn("风险许可：本节点未登记新的正式决策", normalized)
            self.assertIn("  - 缺失盈亏ETF（111111）：事实不足", normalized)
            self.assertIn("唯一主候选：无新的主候选", normalized)
            self.assertNotIn("FEE_old", normalized)
            self.assertIn("<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->\n<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->", normalized)

    def test_experience_projection_empties_case_intake_and_splits_compact_case_heading(self):
        text = (
            "# E\n"
            "<!-- AUTO_CASE_INTAKE_START -->\n"
            "trade_1｜- 待复盘CASE｜机器路由行\n"
            "<!-- AUTO_CASE_INTAKE_END -->\n"
            "### 2.18 CASE-20260909-01：煤炭ETF（515220）Trial执行复盘｜2026-09-09｜13:24形成Trial决策，13:30真实买入。\n"
        )
        normalized = normalize_human_readable_projection(Path("."), "ETF交易复盘与经验库_2026.md", text)
        self.assertIn("<!-- AUTO_CASE_INTAKE_START -->\n<!-- AUTO_CASE_INTAKE_END -->", normalized)
        self.assertNotIn("待复盘CASE", normalized)
        self.assertIn("### 2.18 CASE-20260909-01：煤炭ETF（515220）Trial执行复盘\n", normalized)
        self.assertIn("- 摘要：2026-09-09；13:24形成Trial决策，13:30真实买入。", normalized)

    def test_noop_write_still_repairs_existing_human_projection_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "ETF交易复盘与经验库_2026.md"
            dirty = (
                "# E\n"
                "<!-- AUTO_CASE_INTAKE_START -->\n"
                "trade_1｜- 待复盘CASE｜机器路由行\n"
                "<!-- AUTO_CASE_INTAKE_END -->\n"
            )
            (root / name).write_text(dirty, encoding="utf-8")
            self.assertTrue(write_formal_text_if_changed(root, name, dirty))
            result = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("待复盘CASE", result)
            self.assertFalse(write_formal_text_if_changed(root, name, result))

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
