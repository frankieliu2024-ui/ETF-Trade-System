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
        self.assertEqual(trade["production_adapter"], "scripts/run_scheduled_trade_review_actor.py")
        self.assertEqual(trade["completion_builder"], "build_trade_review_completion_request")
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

    def test_trade_review_production_gate_and_canonical_scenario_are_explicit(self):
        text = (ROOT / "docs/ETF任务入口与调度治理协议_V1.0.md").read_text(encoding="utf-8")
        self.assertIn("Trade Review生产入口与用户显示硬门", text)
        self.assertIn("CHATGPT_SCHEDULED_TRADE_REVIEW_ACTOR", text)
        self.assertIn("build_trade_review_completion_request", text)
        self.assertIn("request_type=SCHEDULED_REVIEW_COMPLETION", text)
        self.assertIn("interaction_scenario=SCHEDULED_TRADE_REVIEW", text)
        self.assertIn("POST_CLOSE_REVIEW", text)
        self.assertIn("仅允许canonical owner历史兼容读取", text)


if __name__ == "__main__":
    unittest.main()
