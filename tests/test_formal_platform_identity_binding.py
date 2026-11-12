"""Pure regression tests for Formal Decision platform/business identity binding."""
from __future__ import annotations

import hashlib
import unittest

from scripts.scheduled_review_completion import validate_formal_runtime_completion_contract


REQUEST_ID = "scheduled-formal-decision-20261112T085539+0800-m4x7q2"
PLATFORM_TASK_ID = "6adb5dbc116c819185f9c6ab45630baa"
FINAL_CONTENT = "observed frozen formal decision content"
CONTENT_HASH = hashlib.sha256(FINAL_CONTENT.encode("utf-8")).hexdigest()


def payload():
    run_id = "scheduled-formal:" + REQUEST_ID
    occurrence = "occurrence:" + REQUEST_ID
    report_id = "ETF_FORMAL_DECISION:" + REQUEST_ID
    binding = {
        "task_id": PLATFORM_TASK_ID,
        "task_name": "ETF正式决策",
        "task_run_id": run_id,
        "occurrence_id": occurrence,
        "request_id": REQUEST_ID,
        "report_id": report_id,
        "report_type": "ETF_FORMAL_DECISION",
        "full_content": FINAL_CONTENT,
        "content_hash": CONTENT_HASH,
    }
    return {
        "task_id": PLATFORM_TASK_ID,
        "task_name": "ETF正式决策",
        "task_run_id": run_id,
        "occurrence_id": occurrence,
        "request_id": REQUEST_ID,
        "market_date": "2026-11-12",
        "requested_at_beijing": "2026-11-12 08:55:39 +0800",
        "runtime_now": "2026-11-12 08:55:39 +0800",
        "source": "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
        "formal_entry_source": "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
        "entry_mode": "AUTOMATIC_SCHEDULED_TASK",
        "interaction_scenario": "FORMAL_DECISION",
        "market_phase": "OUTSIDE_SESSION",
        "decision_path": "FORMAL_CLOSE",
        "report_type": "ETF_FORMAL_DECISION",
        "formal_fact_type": "FORMAL_CURRENT_MAIN_DECISION",
        "report_id": report_id,
        "final_content": FINAL_CONTENT,
        "content_hash": CONTENT_HASH,
        "presentation_binding": binding,
        "fact_binding": {
            "market_date": "2026-11-12",
            "latest_snapshot": "data/market/snapshots/2026-11-12_075621.json",
            "captured_at_beijing": "2026-11-12T07:56:21+08:00",
            "latest_valid_node": "live",
            "fact_status": "READY/PASS",
        },
    }


class FormalPlatformIdentityBindingTests(unittest.TestCase):
    def test_platform_uuid_and_business_name_are_accepted(self):
        candidate = payload()
        validate_formal_runtime_completion_contract(candidate)
        self.assertEqual(candidate["task_id"], PLATFORM_TASK_ID)
        self.assertEqual(candidate["task_name"], "ETF正式决策")
        self.assertEqual(candidate["presentation_binding"]["task_id"], PLATFORM_TASK_ID)
        self.assertEqual(candidate["presentation_binding"]["task_name"], "ETF正式决策")

    def test_mismatched_binding_platform_id_is_rejected(self):
        candidate = payload()
        candidate["presentation_binding"]["task_id"] = "another-platform-task"
        with self.assertRaisesRegex(ValueError, "platform task identity mismatch"):
            validate_formal_runtime_completion_contract(candidate)


if __name__ == "__main__":
    unittest.main()
