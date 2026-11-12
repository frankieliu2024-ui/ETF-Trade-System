"""Pure contract audit for the observed scheduled Formal Decision identity shape.

This file intentionally does not change production validation, event projection,
notification behavior, prompts, or runtime state. It records the current
contract result for the real 2026-11-12 completion shape: the platform supplied
an opaque UUID in task_id and supplied the business identity in task_name.
"""
from __future__ import annotations

import hashlib
import unittest

from scripts.scheduled_review_completion import (
    validate_formal_runtime_completion_contract,
)


REQUEST_ID = "scheduled-formal-decision-20261112T085539+0800-m4x7q2"
PLATFORM_TASK_ID = "6adb5dbc116c819185f9c6ab45630baa"
FINAL_CONTENT = "observed frozen formal decision content"
CONTENT_HASH = hashlib.sha256(FINAL_CONTENT.encode("utf-8")).hexdigest()


def observed_completion() -> dict:
    """Return the observed shape with a complete binding, without writing it."""
    return {
        "task_id": PLATFORM_TASK_ID,
        "task_name": "ETF正式决策",
        "task_run_id": "scheduled-formal:" + REQUEST_ID,
        "occurrence_id": "occurrence:" + REQUEST_ID,
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
        "report_id": "ETF_FORMAL_DECISION:" + REQUEST_ID,
        "final_content": FINAL_CONTENT,
        "content_hash": CONTENT_HASH,
        "presentation_binding": {
            "task_id": PLATFORM_TASK_ID,
            "task_name": "ETF正式决策",
            "task_run_id": "scheduled-formal:" + REQUEST_ID,
            "occurrence_id": "occurrence:" + REQUEST_ID,
            "request_id": REQUEST_ID,
            "report_id": "ETF_FORMAL_DECISION:" + REQUEST_ID,
            "report_type": "ETF_FORMAL_DECISION",
            "full_content": FINAL_CONTENT,
            "content_hash": CONTENT_HASH,
        },
        "fact_binding": {
            "market_date": "2026-11-12",
            "latest_snapshot": "data/market/snapshots/2026-11-12_075621.json",
            "captured_at_beijing": "2026-11-12T07:56:21+08:00",
            "latest_valid_node": "live",
            "fact_status": "READY/PASS",
        },
    }


class FormalPlatformIdentityContractTests(unittest.TestCase):
    def test_observed_uuid_task_id_is_rejected_by_current_validator(self):
        payload = observed_completion()
        self.assertEqual(payload["task_name"], "ETF正式决策")
        self.assertNotEqual(payload["task_id"], "ETF正式决策")
        with self.assertRaisesRegex(
            ValueError, "Formal Decision completion task identity mismatch"
        ):
            validate_formal_runtime_completion_contract(payload)

    def test_observed_shape_has_complete_binding_without_mutating_runtime_state(self):
        payload = observed_completion()
        binding = payload["presentation_binding"]
        self.assertEqual(binding["task_id"], payload["task_id"])
        self.assertEqual(binding["task_name"], payload["task_name"])
        self.assertEqual(binding["report_type"], payload["report_type"])
        self.assertEqual(binding["full_content"], payload["final_content"])
        self.assertEqual(binding["content_hash"], payload["content_hash"])


if __name__ == "__main__":
    unittest.main()
