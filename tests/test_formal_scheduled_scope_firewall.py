import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FormalScheduledScopeFirewallTest(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_index_declares_one_formal_chain_for_three_entries(self):
        text = self.read("ETF_SYSTEM_INDEX.md")
        self.assertIn("三个合法入口统一读取并执行", text)
        self.assertIn("MANUAL_CONVERSATION", text)
        self.assertIn("MANUAL_SCHEDULED_TASK", text)
        self.assertIn("AUTOMATIC_SCHEDULED_TASK", text)
        self.assertIn("STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW", text)
        self.assertIn("三个入口统一禁止", text)

    def test_protocol_declares_one_unified_entry_contract(self):
        text = self.read("docs/Formal Decision执行与用户回复协议.md")
        self.assertIn("## 一、唯一目标", text)
        self.assertIn("MANUAL_CONVERSATION", text)
        self.assertIn("MANUAL_SCHEDULED_TASK", text)
        self.assertIn("AUTOMATIC_SCHEDULED_TASK", text)
        self.assertIn("三个入口统一执行以下链条", text)
        self.assertIn("STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW", text)
        self.assertIn("MARKET_QUOTE_REFRESH", text)
        self.assertIn("不再属于三个入口中的任何一个", text)

    def test_scheduled_contract_rejects_historical_formal_gate_and_bds(self):
        text = self.read("docs/formal-decision-scheduled-task-rebuild-v2.md")
        self.assertIn("## 0. 作用域防火墙（强制）", text)
        self.assertIn("不得读取、等待、复用或消费 `MANUAL_CONVERSATION` 的 `formal_reply_gate`", text)
        self.assertIn("不得创建或消费 BDS", text)
        self.assertIn("`STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW`", text)
        self.assertIn("不得把历史 DWP、formal_reply_gate 或对话父请求绑定进来", text)


if __name__ == "__main__":
    unittest.main()
