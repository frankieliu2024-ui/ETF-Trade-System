import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TaskEntrySchedulingGovernanceTests(unittest.TestCase):
    def test_task_schedule_mirror_matches_current_frozen_contract(self):
        data = json.loads((ROOT / "config/maintenance/task_entry_scheduling_protocol.json").read_text(encoding="utf-8"))
        self.assertEqual([x["task_name"] for x in data["tasks"]], ["ETF系统复核", "ETF交易复盘", "ETF正式决策"])
        self.assertEqual(data["tasks"][2]["automatic_schedule"], ["09:00", "10:00", "11:00", "13:30", "14:30"])
        system = data["tasks"][0]
        self.assertEqual(system["production_adapter"], "scripts/run_scheduled_system_review_actor.py")
        self.assertEqual(system["completion_builder"], "build_system_review_completion_request")
        self.assertEqual(system["handoff_execution"]["report_identity"], "ETF_SYSTEM_REVIEW")
        trade = data["tasks"][1]
        self.assertEqual(trade["completion_owner"], "SHARED_SCHEDULED_REVIEW_COMPLETION_OWNER")
        self.assertEqual(trade["production_adapter"], "scripts/run_scheduled_trade_review_actor.py")
        decision = data["tasks"][2]
        self.assertNotIn("entry_adapter", decision)
        self.assertNotIn("entry_builder", decision)
        self.assertEqual(decision["production_adapter"], "scripts/run_scheduled_formal_decision_actor.py")
        self.assertNotIn("completion_builder", decision)
        facts = decision["fact_consumption"]
        self.assertFalse(facts["request_required"])
        self.assertFalse(facts["parent_request_required"])
        self.assertFalse(facts["market_snapshot_request_required"])
        self.assertFalse(facts["request_bound_context_required"])
        self.assertIn("0_to_900_seconds_normal", facts["freshness_decision"])
        self.assertIn("901_to_1500_seconds_degraded", facts["freshness_decision"])

    def test_protocol_preserves_manual_and_automatic_boundaries(self):
        text = (ROOT / "docs/ETF任务入口与调度治理协议_V1.0.md").read_text(encoding="utf-8")
        for phrase in ("手动入口不得因为当前时间不是自动节点而SKIP", "不跨任务复用身份", "不得自动执行证券交易"):
            self.assertIn(phrase, text)
        for phrase in ("CHATGPT_MANUAL_FORMAL_ANALYSIS", "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
                       "CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "同任务同正文合同",
                       "SCHEDULED_REVIEW_COMPLETION"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
