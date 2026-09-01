import tempfile
import unittest
from pathlib import Path

from scripts import runtime_self_heal
from scripts.rules_version import parse_master_release


BASE = """# ETF波段交易系统 V2.2.31 规则 MASTER
> 更新日期：2026-09-01
> 定位：V2.2.31 待结算现金约束与新股兑现证据正式吸收版；本文件为现行交易规则唯一来源。

|版本|定位|状态|
|-|-|-|
|V2.2.30|旧版|历史|
|V2.2.31|新版|当前版本与现行交易规则|

V2.2.30旧说明。
V2.2.31完成待结算现金约束与新股兑现证据正式吸收。
"""


class RulesVersionContractTests(unittest.TestCase):
    def test_current_release_is_parsed_from_structured_metadata(self):
        parsed = parse_master_release(BASE)
        self.assertTrue(parsed["ok"], parsed["errors"])
        self.assertEqual(parsed["version"], "V2.2.31")
        self.assertTrue(parsed["previous_version_present"])
        self.assertTrue(parsed["current_description_present"])

    def test_positioning_drift_fails(self):
        parsed = parse_master_release(BASE.replace("定位：V2.2.31", "定位：V2.2.30"))
        self.assertFalse(parsed["ok"])

    def test_current_table_drift_fails(self):
        parsed = parse_master_release(BASE.replace("|V2.2.31|新版|当前版本与现行交易规则|", "|V2.2.30|新版|当前版本与现行交易规则|"))
        self.assertFalse(parsed["ok"])

    def test_missing_previous_history_and_description_fail(self):
        broken = BASE.replace("|V2.2.30|旧版|历史|\n", "").replace("V2.2.31完成", "V2.2.32完成")
        parsed = parse_master_release(broken)
        self.assertFalse(parsed["ok"])
        self.assertFalse(parsed["previous_version_present"])
        self.assertFalse(parsed["current_description_present"])

    def test_historical_version_does_not_become_current(self):
        parsed = parse_master_release(BASE + "\n历史记录 V2.2.18_CLOSE_REVIEW\n")
        self.assertEqual(parsed["version"], "V2.2.31")

    def test_self_heal_uses_structured_parser_and_flags_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "ETF规则_MASTER.md"
            master.write_text(BASE, encoding="utf-8")
            old_master_path = runtime_self_heal.MASTER_PATH
            runtime_self_heal.MASTER_PATH = master
            try:
                self.assertEqual(runtime_self_heal.master_version(), "V2.2.31")
            finally:
                runtime_self_heal.MASTER_PATH = old_master_path

    def test_formal_files_do_not_claim_old_rule_version(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("ETF交易复盘与经验库_2026.md", "ETF市场行情档案_2026.md"):
            first_lines = (root / name).read_text(encoding="utf-8").splitlines()[:5]
            self.assertTrue(any(line.startswith("> 文件结构版本：") for line in first_lines))
            self.assertFalse(any("版本：ETF波段交易系统 V2.2.18" in line for line in first_lines))


if __name__ == "__main__":
    unittest.main()

