import hashlib
import unittest
from pathlib import Path

from scripts.scheduled_review_completion import (
    build_formal_decision_scheduled_review_completion_request,
)


class FormalDecisionCompletionIngressTests(unittest.TestCase):
    def test_three_entry_completion_uses_one_scheduled_review_contract(self):
        payload = build_formal_decision_scheduled_review_completion_request(
            {"action": "新增0元"},
            "formal-request",
            "2026-10-16T11:30:00+08:00",
            "2026-10-16",
            "ETF正式决策",
            "manual-scheduled-20261016T113000+0800-ab12cd34",
            "FINAL_CONTENT",
            entry_mode="MANUAL_SCHEDULED_TASK",
            trigger_source="CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
            occurrence_id="manual-scheduled-20261016T113000+0800-ab12cd34",
        )
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_REVIEW")
        self.assertEqual(payload["source"], "CHATGPT_SCHEDULED_REVIEW_ACTOR")
        self.assertEqual(payload["interaction_scenario"], "FORMAL_DECISION")
        self.assertEqual(payload["occurrence_id"], payload["occurrence"])
        self.assertEqual(
            payload["presentation_binding"]["content_hash"],
            hashlib.sha256(b"FINAL_CONTENT").hexdigest(),
        )

    def test_index_routes_formal_only_to_unified_chain(self):
        index = Path("ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")
        self.assertIn("三个入口统一读取并执行", index)
        self.assertIn("STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW", index)
        self.assertIn("PushPlus", index)
        self.assertNotIn("BUSINESS_DECISION_SOURCE/BDS → FORMAL_DECISION_COMPLETION", index)
        self.assertNotIn("CHATGPT_MANUAL_FORMAL_COMPLETION", index)

    def test_process_owner_requires_formal_entry_identity(self):
        text = Path("scripts/process_state_sync_request.py").read_text(encoding="utf-8")
        self.assertIn("Formal Scheduled Review entry identity is invalid", text)
        self.assertIn("Formal Scheduled Review occurrence identity is required", text)
        self.assertIn("Formal Scheduled Review occurrence binding mismatch", text)


if __name__ == "__main__":
    unittest.main()
