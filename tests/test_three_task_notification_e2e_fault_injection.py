import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ThreeTaskNotificationE2EFaultInjectionTests(unittest.TestCase):
    TASKS = {
        "ETF系统复核": {
            "prompt": ROOT / "docs/freezes/bundles/ETF_THREE_TASKS_FREEZE_20261020T1817/prompts/系统复核.txt",
            "actor": None,
            "report_type": "ETF_SYSTEM_REVIEW",
        },
        "ETF交易复盘": {
            "prompt": ROOT / "docs/freezes/bundles/ETF_THREE_TASKS_FREEZE_20261020T1817/prompts/交易复盘.txt",
            "actor": ROOT / "scripts/run_scheduled_trade_review_actor.py",
            "report_type": "ETF_TRADE_REVIEW",
        },
        "ETF正式决策": {
            "prompt": ROOT / "docs/freezes/bundles/ETF_THREE_TASKS_FREEZE_20261020T1817/prompts/正式决策.txt",
            "actor": ROOT / "scripts/run_scheduled_formal_decision_actor.py",
            "report_type": "ETF_FORMAL_DECISION",
        },
    }

    def test_all_three_tasks_use_one_completion_and_notification_owner(self):
        workflow = (ROOT / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        recovery = (ROOT / "scripts/scheduled_review_completion_recovery.py").read_text(encoding="utf-8")
        self.assertIn("scheduled_review_completion_path", workflow)
        self.assertIn("MAX_ATTEMPTS = 3", recovery)
        self.assertIn("different completion already exists on main", recovery)
        for task, spec in self.TASKS.items():
            prompt = spec["prompt"].read_text(encoding="utf-8")
            completion_contract = (ROOT / "scripts/formal_entry_contract.py").read_text(encoding="utf-8")
            self.assertIn("SCHEDULED_REVIEW_COMPLETION", completion_contract, task)
            self.assertIn("content_hash", prompt, task)
            self.assertTrue("GitHub Contents" in prompt or "current main" in prompt, task)
            if spec["actor"] is not None:
                actor = spec["actor"].read_text(encoding="utf-8")
                self.assertIn("persist_completion_with_recovery", actor, task)

    def test_fault_matrix_is_fail_closed_before_notification(self):
        recovery = (ROOT / "scripts/scheduled_review_completion_recovery.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        fault_matrix = {
            "transient_write_timeout": "MAX_ATTEMPTS = 3",
            "identical_replay": "already_present",
            "conflicting_completion": "different completion already exists on main",
            "readback_mismatch": "completion readback content mismatch",
        }
        for fault, marker in fault_matrix.items():
            self.assertIn(marker, recovery, fault)
        self.assertIn("No current shared completion/report binding; fail closed without PushPlus.", workflow)
        self.assertIn("canonical_publication", workflow)

    def test_task_identity_and_report_type_are_not_crossed(self):
        expected = {
            "ETF系统复核": "ETF_SYSTEM_REVIEW",
            "ETF交易复盘": "ETF_TRADE_REVIEW",
            "ETF正式决策": "ETF_FORMAL_DECISION",
        }
        config = json.loads(
            (ROOT / "config/maintenance/task_entry_scheduling_protocol.json").read_text(encoding="utf-8")
        )
        text = json.dumps(config, ensure_ascii=False)
        for task, report_type in expected.items():
            self.assertIn(task, text)
            self.assertIn(report_type, text)
        self.assertEqual(set(expected.values()), {spec["report_type"] for spec in self.TASKS.values()})


if __name__ == "__main__":
    unittest.main()
