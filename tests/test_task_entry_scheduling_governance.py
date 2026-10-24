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
        self.assertEqual(data["tasks"][2]["automatic_schedule"], ["09:00", "10:00", "11:00", "13:30", "14:30"])
        self.assertTrue(data["tasks"][0]["content_contract"]["manual_and_automatic_same_contract"])
        self.assertTrue(data["tasks"][0]["content_contract"]["validation_only_report_forbidden"])
        trade = data["tasks"][1]
        self.assertEqual(trade["completion_owner"], "SHARED_SCHEDULED_REVIEW_COMPLETION_OWNER")
        self.assertNotIn("entry_adapter", trade)
        self.assertNotIn("production_adapter", trade)
        self.assertNotIn("completion_builder", trade)
        self.assertEqual(trade["handoff_contract"]["owner"], "SHARED_SCHEDULED_REVIEW_COMPLETION_OWNER")
        decision = data["tasks"][2]
        self.assertEqual(decision["entry_adapter"], "scripts/create_scheduled_formal_request.py")
        self.assertEqual(decision["entry_builder"], "build_scheduled_request")
        self.assertEqual(decision["production_adapter"], "scripts/run_scheduled_formal_decision_actor.py")
        self.assertEqual(decision["completion_builder"], "build_formal_review_completion_request")
    def test_protocol_preserves_manual_and_automatic_boundaries(self):
        text = (ROOT / "docs/ETF任务入口与调度治理协议_V1.0.md").read_text(encoding="utf-8")
        for phrase in ("手动入口不得因为当前时间不是自动节点而SKIP", "不跨任务复用身份", "不得自动执行证券交易"):
            self.assertIn(phrase, text)
        self.assertIn("CHATGPT_MANUAL_FORMAL_ANALYSIS", text)
        self.assertIn("CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", text)
        self.assertIn("CHATGPT_SCHEDULED_FORMAL_ANALYSIS", text)
        self.assertIn("同任务同正文合同", text)
        self.assertIn("MANUAL_SYSTEM_REVIEW_CHAIN_VALIDATION", text)
        self.assertIn("不得使用", text)
        self.assertIn("Scheduled Review后置交接合同", text)
        self.assertIn("SCHEDULED_REVIEW_COMPLETION", text)

        def test_trade_review_uses_shared_completion_contract(self):
        data = json.loads((ROOT / "config/maintenance/task_entry_scheduling_protocol.json").read_text(encoding="utf-8"))
        trade = data["tasks"][1]
        self.assertEqual(trade["route"], "TRADE_REVIEW")
        self.assertEqual(trade["completion_owner"], "SHARED_SCHEDULED_REVIEW_COMPLETION_OWNER")
        self.assertEqual(trade["handoff_contract"]["owner"], "SHARED_SCHEDULED_REVIEW_COMPLETION_OWNER")
        self.assertEqual(trade["entry_modes"]["manual"]["entry_mode"], "MANUAL_SCHEDULED_TASK")
        self.assertEqual(trade["entry_modes"]["automatic"]["entry_mode"], "AUTOMATIC_SCHEDULED_TASK")

if __name__ == "__main__":
    unittest.main()
