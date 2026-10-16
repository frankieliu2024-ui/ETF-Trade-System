import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scripts.create_scheduled_formal_request import (
    SLOTS,
    build_scheduled_request,
    classify_schedule_invocation,
)


class FormalScheduledTaskFromZeroContractTests(unittest.TestCase):
    def test_exact_four_utc_cron_slots_map_to_beijing_nodes(self):
        self.assertEqual(SLOTS, {
            "0 2 * * 1-5": (10, 0),
            "0 3 * * 1-5": (11, 0),
            "30 5 * * 1-5": (13, 30),
            "30 6 * * 1-5": (14, 30),
        })

    def test_automatic_non_slot_is_skipped(self):
        self.assertEqual(
            classify_schedule_invocation("AUTOMATIC_SCHEDULED_TASK", ""),
            "SKIP",
        )

    def test_manual_scheduled_task_is_not_limited_by_automatic_slot(self):
        self.assertEqual(
            classify_schedule_invocation("MANUAL_SCHEDULED_TASK", ""),
            "RUN",
        )

    def test_builder_emits_both_occurrence_fields_consistently(self):
        now = datetime(2026, 10, 16, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        payload = build_scheduled_request(
            now=now,
            task_id="task-1",
            task_run_id="run-1",
            occurrence_id="scheduled_2026-10-16T10:00:00+08:00",
        )
        self.assertEqual(payload["source"], "CHATGPT_SCHEDULED_REVIEW_ACTOR")
        self.assertEqual(payload["trigger_source"], "CHATGPT_SCHEDULED_FORMAL_ANALYSIS")
        self.assertEqual(payload["entry_mode"], "AUTOMATIC_SCHEDULED_TASK")
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_REVIEW")
        self.assertEqual(payload["occurrence_id"], payload["occurrence"])
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["task_run_id"], "run-1")
        self.assertNotIn("force_refresh", payload)
        self.assertNotIn("require_post_request_snapshot", payload)


    def test_manual_builder_is_state_sync_and_not_slot_limited(self):
        now = datetime(2026, 10, 16, 11, 30, tzinfo=timezone(timedelta(hours=8)))
        payload = build_scheduled_request(
            now=now,
            task_id="ETF正式决策",
            task_run_id="manual-scheduled-20261016T113000+0800-ab12cd34",
            occurrence_id="manual-scheduled-20261016T113000+0800-ab12cd34",
            entry_mode="MANUAL_SCHEDULED_TASK",
        )
        self.assertEqual(payload["source"], "CHATGPT_SCHEDULED_REVIEW_ACTOR")
        self.assertEqual(
            payload["trigger_source"],
            "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
        )
        self.assertEqual(payload["entry_mode"], "MANUAL_SCHEDULED_TASK")
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_REVIEW")
        self.assertNotIn("force_refresh", payload)
        self.assertNotIn("query_intent", payload)


    def test_conversation_builder_uses_the_same_scheduled_review_contract(self):
        now = datetime(2026, 10, 16, 11, 30, tzinfo=timezone(timedelta(hours=8)))
        payload = build_scheduled_request(
            now=now,
            task_id="ETF正式决策",
            task_run_id="conversation-20261016T113000-ab12cd34",
            occurrence_id="conversation-20261016T113000+0800-ab12cd34",
            entry_mode="MANUAL_CONVERSATION",
        )
        self.assertEqual(payload["trigger_source"], "CHATGPT_MANUAL_FORMAL_ANALYSIS")
        self.assertEqual(payload["entry_mode"], "MANUAL_CONVERSATION")
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_REVIEW")

    def test_conversation_identity_is_not_scheduled_task_identity(self):
        now = datetime(2026, 10, 16, 11, 30, tzinfo=timezone(timedelta(hours=8)))
        payload = build_scheduled_request(
            now=now,
            task_id="ETF正式决策",
            task_run_id="conversation-20261016T113000+0800-ab12cd34",
            occurrence_id="conversation-20261016T113000+0800-ab12cd34",
            entry_mode="MANUAL_CONVERSATION",
        )
        self.assertTrue(payload["occurrence_id"].startswith("conversation-"))
        self.assertNotIn("scheduled_", payload["occurrence_id"])

    def test_manual_occurrence_cannot_impersonate_automatic(self):
        now = datetime(2026, 10, 16, 11, 30, tzinfo=timezone(timedelta(hours=8)))
        with self.assertRaisesRegex(ValueError, "manual occurrence"):
            build_scheduled_request(
                now=now,
                task_id="ETF正式决策",
                task_run_id="manual-scheduled-run",
                occurrence_id="scheduled_20261016T113000",
                entry_mode="MANUAL_SCHEDULED_TASK",
            )

    def test_payload_is_json_serializable(self):
        now = datetime(2026, 10, 16, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        payload = build_scheduled_request(
            now=now,
            task_id="task-1",
            task_run_id="run-1",
            occurrence_id="scheduled_2026-10-16T10:00:00+08:00",
        )
        json.dumps(payload, ensure_ascii=False)

    def test_task_stays_independent_from_stable_review_tasks(self):
        contract = Path("docs/formal-decision-scheduled-task-rebuild-v2.md").read_text(encoding="utf-8")
        self.assertIn("稳定任务的实现、状态和测试必须保持不变", contract)
        self.assertIn("不得把旧的", contract)


if __name__ == "__main__":
    unittest.main()
