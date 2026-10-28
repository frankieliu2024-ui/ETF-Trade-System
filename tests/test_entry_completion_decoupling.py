import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class EntryCompletionDecouplingTests(unittest.TestCase):
    def test_shared_trade_completion_accepts_all_registered_entry_modes(self):
        source = (ROOT / "scripts" / "scheduled_review_completion.py").read_text(encoding="utf-8")
        self.assertIn(
            'if effective_entry_mode not in {"MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"}:',
            source,
        )
        self.assertNotIn(
            'if effective_entry_mode not in {"MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"}:',
            source,
        )

    def test_system_actor_accepts_conversation_entry(self):
        source = (ROOT / "scripts" / "run_scheduled_system_review_actor.py").read_text(encoding="utf-8")
        self.assertIn(
            'choices=("MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK")',
            source,
        )

    def test_task_protocol_registers_conversation_entries_without_changing_schedule(self):
        protocol = json.loads(
            (ROOT / "config" / "maintenance" / "task_entry_scheduling_protocol.json").read_text(
                encoding="utf-8"
            )
        )
        for name in ("ETF系统复核", "ETF交易复盘"):
            task = next(item for item in protocol["tasks"] if item["task_name"] == name)
            self.assertEqual(task["entry_modes"]["manual_conversation"]["entry_mode"], "MANUAL_CONVERSATION")
            self.assertTrue(task["handoff_contract"]["entry_mode_is_identity_only"])
            self.assertTrue(task["handoff_contract"]["completion_owner_must_accept_registered_entry_modes"])
        trade = next(item for item in protocol["tasks"] if item["task_name"] == "ETF交易复盘")
        self.assertEqual(trade["automatic_schedule"], ["12:30", "20:30"])
        system = next(item for item in protocol["tasks"] if item["task_name"] == "ETF系统复核")
        self.assertEqual(system["automatic_schedule"], ["08:30", "19:30"])


if __name__ == "__main__":
    unittest.main()
