import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TaskEntrySchedulingGovernanceTests(unittest.TestCase):
    def test_task_schedule_mirror_matches_current_frozen_contract(self):
        data = json.loads((ROOT / "config/maintenance/task_entry_scheduling_protocol.json").read_text(encoding="utf-8"))
        self.assertEqual([x["task_name"] for x in data["tasks"]], ["ETF系统复核", "ETF交易复盘", "ETF正式决策"])
        self.assertEqual(data["tasks"][0]["automatic_schedule"], ["08:30", "19:30"])
        self.assertEqual(data["tasks"][1]["automatic_schedule"], ["12:30", "20:30"])
        self.assertEqual(data["tasks"][2]["automatic_schedule"], ["09:00", "10:00", "11:00", "13:30", "14:30"])\n        self.assertTrue(data["tasks"][0]["content_contract"]["manual_and_automatic_same_contract"])\n        self.assertTrue(data["tasks"][0]["content_contract"]["validation_only_report_forbidden"])\n
    def test_protocol_preserves_manual_and_automatic_boundaries(self):
        text = (ROOT / "docs/ETF任务入口与调度治理协议_V1.0.md").read_text(encoding="utf-8")
        for phrase in ("手动入口不得因为当前时间不是自动节点而SKIP", "不跨任务复用身份", "不得自动执行证券交易"):
            self.assertIn(phrase, text)
        self.assertIn("CHATGPT_MANUAL_FORMAL_ANALYSIS", text)
        self.assertIn("CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", text)
        self.assertIn("CHATGPT_SCHEDULED_FORMAL_ANALYSIS", text)\n        self.assertIn("同任务同正文合同", text)\n        self.assertIn("MANUAL_SYSTEM_REVIEW_CHAIN_VALIDATION", text)\n        self.assertIn("不得使用", text)\n        self.assertIn("Scheduled Review后置交接合同", text)\n        self.assertIn("SCHEDULED_REVIEW_COMPLETION", text)\n

if __name__ == "__main__":
    unittest.main()
