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
            occurrence_id="2026-10-16T10:00:00+08:00",
        )
        self.assertEqual(payload["source"], "CHATGPT_SCHEDULED_FORMAL_ANALYSIS")
        self.assertEqual(payload["entry_mode"], "AUTOMATIC_SCHEDULED_TASK")
        self.assertEqual(payload["occurrence_id"], payload["occurrence"])
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["task_run_id"], "run-1")
        self.assertTrue(payload["force_refresh"])
        self.assertTrue(payload["require_post_request_snapshot"])

    def test_payload_is_json_serializable(self):
        now = datetime(2026, 10, 16, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        payload = build_scheduled_request(
            now=now,
            task_id="task-1",
            task_run_id="run-1",
            occurrence_id="2026-10-16T10:00:00+08:00",
        )
        json.dumps(payload, ensure_ascii=False)

    def test_task_stays_independent_from_stable_review_tasks(self):
        prompt = Path("docs/Formal Decision定时任务从零重建说明_V1.0.md").read_text(encoding="utf-8")
        self.assertIn("不修改【ETF系统复核】、【ETF交易复盘】", prompt)


if __name__ == "__main__":
    unittest.main()
