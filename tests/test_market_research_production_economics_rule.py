from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "ETF规则_MASTER.md"


class ResearchProductionEconomicsRuleTests(unittest.TestCase):
    def test_master_requires_production_availability_and_economics_review(self):
        text = MASTER.read_text(encoding="utf-8")
        self.assertIn("生产可获得性与收益成本比审查", text)
        self.assertIn("研究有效不等于值得生产化", text)
        self.assertIn("沉没成本不构成继续扩建理由", text)
        self.assertIn("不得为了“自动化完整”本身无限新增provider、workflow、缓存、checker或状态副本", text)

    def test_rule_does_not_create_new_trading_authority(self):
        text = MASTER.read_text(encoding="utf-8")
        self.assertIn("停止追加工程不自动撤销其历史研究资格，也不创造交易权限", text)
        self.assertIn("V2.2.28", text)


if __name__ == "__main__":
    unittest.main()
