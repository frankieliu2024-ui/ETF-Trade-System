import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_SPEC = ROOT / "ETF与市场监测数据接口使用规范.md"
RECOVERY = ROOT / "scripts" / "historical_market_fact_recovery.py"
HISTORY_ENTRY = ROOT / "scripts" / "hithink_etf_data.py"
POOL_FETCH = ROOT / "scripts" / "fetch_v2214_pool_data.py"


class ResearchHistoricalDataPreflightContractTest(unittest.TestCase):
    def test_data_spec_requires_preflight_before_historical_stop(self):
        text = DATA_SPEC.read_text(encoding="utf-8")
        self.assertIn("### 8.2 研究历史数据预检（强制）", text)
        for marker in (
            "现有正式与研究态历史数据入口",
            "共同历史窗口",
            "PIT边界",
            "仓库中没有CSV、Excel或研究输出文件，不等于历史事实不可得",
            "按数据粒度分别判断",
            "不得重建历史ChatGPT判断",
            "不得建立第二历史数据链",
        ):
            self.assertIn(marker, text)

    def test_recovery_contract_separates_daily_and_exact_intraday(self):
        text = RECOVERY.read_text(encoding="utf-8")
        self.assertIn("RECOVERABLE_EXACT", text)
        self.assertIn("RECOVERABLE_EOD_EQUIVALENT", text)
        self.assertIn("historical_retrieved_at_beijing", text)
        self.assertIn("original_provider_observation_time_beijing", text)
        self.assertIn("can_promote_to_current", text)
        self.assertIn("read_only", text)

    def test_existing_research_entry_is_discoverable_without_production_promotion(self):
        history = HISTORY_ENTRY.read_text(encoding="utf-8")
        fetch = POOL_FETCH.read_text(encoding="utf-8")
        self.assertIn("def history(", history)
        self.assertIn("interval", history)
        self.assertIn("HithinkETFClient", fetch)
        self.assertIn("fetch_hithink", fetch)
        self.assertIn("专项回测", fetch)
        self.assertNotIn("CURRENT.json", fetch)

    def test_minute_capability_is_not_implied_by_daily_entry(self):
        text = DATA_SPEC.read_text(encoding="utf-8")
        section = text.split("### 8.2 研究历史数据预检（强制）", 1)[1].split("\n## 9.", 1)[0]
        self.assertIn("日线或收盘等价事实不得冒充分钟或盘中事实", section)
        self.assertIn("completed-bar", section)


if __name__ == "__main__":
    unittest.main()
