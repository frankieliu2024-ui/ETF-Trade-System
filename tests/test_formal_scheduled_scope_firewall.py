import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FormalScheduledScopeFirewallTest(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_index_scopes_market_quote_chain_to_manual_conversation(self):
        text = self.read("ETF_SYSTEM_INDEX.md")
        self.assertIn("Formal Decision 场景作用域防火墙（强制）", text)
        self.assertIn("仅适用于入口表中的 `entry_mode=MANUAL_CONVERSATION`", text)
        self.assertIn("MANUAL_SCHEDULED_TASK", text)
        self.assertIn("STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW", text)
        self.assertIn("不得读取、等待、复用或消费本节的历史 `formal_reply_gate`", text)

    def test_protocol_declares_two_non_interchangeable_entry_contracts(self):
        text = self.read("docs/Formal Decision执行与用户回复协议.md")
        self.assertIn("## 场景作用域防火墙（强制）", text)
        self.assertIn("MANUAL_CONVERSATION", text)
        self.assertIn("MANUAL_SCHEDULED_TASK / AUTOMATIC_SCHEDULED_TASK", text)
        self.assertIn("默认仅适用于 `MANUAL_CONVERSATION`", text)
        self.assertIn("Scheduled Review completion", text)

    def test_scheduled_contract_rejects_historical_formal_gate_and_bds(self):
        text = self.read("docs/formal-decision-scheduled-task-rebuild-v2.md")
        self.assertIn("## 0. 作用域防火墙（强制）", text)
        self.assertIn("不得读取、等待、复用或消费 `MANUAL_CONVERSATION` 的 `formal_reply_gate`", text)
        self.assertIn("不得创建或消费 BDS", text)
        self.assertIn("`STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW`", text)
        self.assertIn("不得把历史 DWP、formal_reply_gate 或对话父请求绑定进来", text)


if __name__ == "__main__":
    unittest.main()
