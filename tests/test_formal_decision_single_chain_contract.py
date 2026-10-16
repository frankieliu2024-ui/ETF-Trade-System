import unittest
from datetime import datetime, timezone, timedelta

from scripts.create_scheduled_formal_request import build_scheduled_request
from scripts.formal_entry_contract import (
    validate_formal_completion_request,
    validate_formal_parent_request,
)


class FormalDecisionSingleChainContractTests(unittest.TestCase):
    def _parent(self, mode, source, prefix):
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        occurrence = f"{prefix}-20261016T120000+0800-ab12cd34"
        payload = build_scheduled_request(
            now=now,
            task_id="ETF正式决策",
            task_run_id=occurrence,
            occurrence_id=occurrence,
            entry_mode=mode,
        )
        payload["trigger_source"] = source
        return payload

    def test_all_three_entries_share_one_canonical_parent_contract(self):
        cases = (
            ("MANUAL_CONVERSATION", "CHATGPT_MANUAL_FORMAL_ANALYSIS", "conversation"),
            (
                "MANUAL_SCHEDULED_TASK",
                "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
                "manual-scheduled",
            ),
            (
                "AUTOMATIC_SCHEDULED_TASK",
                "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
                "scheduled_",
            ),
        )
        for mode, source, prefix in cases:
            valid, reason = validate_formal_parent_request(
                self._parent(mode, source, prefix)
            )
            self.assertTrue(valid, (mode, reason))

    def test_parent_request_is_not_completion(self):
        payload = self._parent(
            "MANUAL_CONVERSATION",
            "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "conversation",
        )
        valid, reason = validate_formal_completion_request(payload)
        self.assertFalse(valid)
        self.assertEqual(
            reason, "formal_completion_request_type_must_be_STATE_SYNC_ONLY"
        )

    def test_completion_contract_is_distinct_and_bound_to_parent_identity(self):
        parent = self._parent(
            "MANUAL_CONVERSATION",
            "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "conversation",
        )
        completion = {
            "request_id": parent["request_id"] + "__formal_decision_completion",
            "parent_request_id": parent["request_id"],
            "request_type": "STATE_SYNC_ONLY",
            "source": "CHATGPT_FORMAL_DECISION_COMPLETION",
            "formal_fact_type": "FORMAL_DECISION_COMPLETION",
            "entry_mode": parent["entry_mode"],
            "task_id": parent["task_id"],
            "task_run_id": parent["task_run_id"],
            "occurrence": parent["occurrence"],
            "occurrence_id": parent["occurrence_id"],
            "market_date": parent["market_date"],
            "bds_path": "requests/live_snapshot/parent__business_decision_source.json",
            "decision_id": "parent__decision",
            "content_hash": "a" * 64,
        }
        valid, reason = validate_formal_completion_request(completion)
        self.assertTrue(valid, reason)
        self.assertNotEqual(completion["request_id"], completion["parent_request_id"])
        self.assertEqual(completion["task_run_id"], parent["task_run_id"])
        self.assertEqual(completion["occurrence_id"], parent["occurrence_id"])

    def test_completion_rejects_parent_request_type(self):
        parent = self._parent(
            "MANUAL_CONVERSATION",
            "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "conversation",
        )
        parent["formal_fact_type"] = "FORMAL_DECISION_COMPLETION"
        parent["source"] = "CHATGPT_FORMAL_DECISION_COMPLETION"
        valid, reason = validate_formal_completion_request(parent)
        self.assertFalse(valid)
        self.assertEqual(
            reason, "formal_completion_request_type_must_be_STATE_SYNC_ONLY"
        )


if __name__ == "__main__":
    unittest.main()
