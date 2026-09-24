import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ThreeLayerSemanticConvergenceTests(unittest.TestCase):
    def test_master_uses_business_three_layers(self):
        text = (ROOT / "ETF规则_MASTER.md").read_text(encoding="utf-8")
        self.assertIn("第一层：外部驱动与跨市场状态", text)
        self.assertIn("第二层：A股内部市场状态", text)
        self.assertIn("第三层：ETF机会与资本状态", text)
        self.assertIn("实际持有个股由合法账户事实动态识别", text)
        self.assertIn("条件产业链个股仅作为Triggered Evidence", text)
        self.assertNotIn("云端市场监测固定为三层：第一层指数；第二层ETF；第三层个股。", text)

    def test_data_spec_owns_data_eligibility_not_actions(self):
        text = (ROOT / "ETF与市场监测数据接口使用规范.md").read_text(encoding="utf-8")
        self.assertIn("第一层：外部驱动与跨市场状态数据", text)
        self.assertIn("第二层：A股内部市场状态数据", text)
        self.assertIn("第三层：ETF机会与资本状态数据", text)
        self.assertIn("provider_as_of", text)
        self.assertIn("execution_eligibility", text)
        self.assertIn("不得复制MASTER中的风险许可", text)
        self.assertNotIn("### 2.1 第一层：指数", text)
        self.assertNotIn("### 2.2 第二层：ETF", text)
        self.assertNotIn("### 2.3 第三层：个股", text)


if __name__ == "__main__":
    unittest.main()
