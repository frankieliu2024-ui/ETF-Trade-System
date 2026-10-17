import json
import unittest
from datetime import datetime, timezone, timedelta

from scripts.create_scheduled_formal_request import (
    SLOTS,
    build_scheduled_request,
    classify_schedule_invocation,
)


class FormalScheduledTaskContractTests(unittest.TestCase):
    def test_five_utc_cron_slots_map_to_beijing_nodes(self):
        self.assertEqual(SLOTS, {
            "0 1 * * 1-5": (9, 0),
            "0 2 * * 1-5": (10, 0),
            "0 3 * * 1-5": (11, 0),
            "30 5 * * 1-5": (13, 30),
            "30 6 * * 1-5": (14, 30),
        })

    def test_0900_automatic_slot_runs(self):
        self.assertEqual(
            classify_schedule_invocation("AUTOMATIC_SCHEDULED_TASK", "0 1 * * 1-5"),
            "RUN",
        )

    def test_automatic_non_slot_is_skipped(self):
        self.assertEqual(
            classify_schedule_invocation("AUTOMATIC_SCHEDULED_TASK", ""),
            "SKIP",
        )

    def test_manual_entry_is_not_limited_by_automatic_slot(self):
        self.assertEqual(classify_schedule_invocation("MANUAL_SCHEDULED_TASK", ""), "RUN")
        self.assertEqual(classify_schedule_invocation("MANUAL_CONVERSATION", ""), "RUN")

    def test_builder_emits_current_formal_request_contract(self):
        now = datetime(2026, 10, 16, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        payload = build_scheduled_request(
            now=now,
            task_id="ETF正式决策",
            task_run_id="run-1",
            occurrence_id="scheduled_2026-10-16T10:00:00+08:00",
        )
        self.assertEqual(payload["source"], "CHATGPT_SCHEDULED_FORMAL_ANALYSIS")
        self.assertEqual(payload["entry_mode"], "AUTOMATIC_SCHEDULED_TASK")
        self.assertEqual(payload["request_type"], "MARKET_QUOTE_REFRESH")
        self.assertEqual(payload["intent"]["interaction_scenario"], "FORMAL_DECISION")
        self.assertTrue(payload["no_trade_authority"])
        self.assertEqual(payload["occurrence_id"], payload["occurrence"])
        json.dumps(payload, ensure_ascii=False)

    def test_manual_occurrence_cannot_impersonate_automatic(self):
        now = datetime(2026, 10, 16, 11, 30, tzinfo=timezone(timedelta(hours=8)))
        with self.assertRaisesRegex(ValueError, "manual occurrence"):
            build_scheduled_request(
                now=now,
                task_id="ETF正式决策",
                task_run_id="manual-run",
                occurrence_id="scheduled_20261016T113000",
                entry_mode="MANUAL_SCHEDULED_TASK",
            )


if __name__ == "__main__":
    unittest.main()
