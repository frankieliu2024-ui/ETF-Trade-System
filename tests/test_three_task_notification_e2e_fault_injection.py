import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ThreeTaskNotificationE2EFaultInjectionTests(unittest.TestCase):
    TASKS = {
        "ETF系统复核": {
            "report_type": "ETF_SYSTEM_REVIEW",
        },
        "ETF交易复盘": {
            "owner": ROOT / "scripts/scheduled_review_completion_owner.py",
            "report_type": "ETF_TRADE_REVIEW",
        },
        "ETF正式决策": {
            "owner": ROOT / "scripts/scheduled_review_completion_owner.py",
            "report_type": "ETF_FORMAL_DECISION",
        },
    }

    def test_all_three_tasks_use_one_completion_and_notification_owner(self):
        workflow = (ROOT / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        recovery = (ROOT / "scripts/scheduled_review_completion_recovery.py").read_text(encoding="utf-8")
        self.assertIn("scheduled_review_completion_path", workflow)
        self.assertIn("MAX_ATTEMPTS = 3", recovery)
        self.assertIn("different completion already exists on main", recovery)
        completion_contract = (ROOT / "scripts/formal_entry_contract.py").read_text(encoding="utf-8")
        self.assertIn("SCHEDULED_REVIEW_COMPLETION", completion_contract)
        self.assertIn("CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER", completion_contract)
        # The shared formal-entry contract is intentionally scoped to Formal Decision;
        # system review and trade review have their own task contracts.
        self.assertIn("ETF正式决策", completion_contract)

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

    def test_discovery_is_regenerated_and_bound_to_each_snapshot(self):
        builder = (ROOT / "scripts/build_weekly_discovery.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("source_snapshot_identity", builder)
        self.assertIn("generation_identity", builder)
        self.assertIn("regeneration_required", builder)
        self.assertIn("must be regenerated and bound to this node snapshot", workflow)

    def test_dispatch_verifies_workflow_run_and_recovers_same_completion(self):
        owner = (ROOT / "scripts/scheduled_review_completion_owner.py").read_text(encoding="utf-8")
        self.assertIn("wait_for_notification_run", owner)
        self.assertIn("workflow_run_created", owner)
        self.assertIn("same-completion recovery", owner)
        self.assertIn("force_resend=True", owner)

    def test_formal_and_system_pushes_are_persistence_only_trade_review_unchanged(self):
        workflow = (ROOT / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn('print("SYSTEM_REVIEW" if task_id == "ETF系统复核" else "TRADE_REVIEW")', workflow)
        self.assertIn('echo "formal_completion=true"', workflow)
        self.assertIn('echo "scheduled_review_completion=false"', workflow)
        self.assertIn('TRADE_REVIEW)', workflow)
        self.assertIn('echo "scheduled_review_completion=true"', workflow)
        self.assertIn("explicit", workflow)
        self.assertIn("scheduled_review_completion_path", workflow)

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
