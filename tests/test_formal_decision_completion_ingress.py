import hashlib
import unittest
from pathlib import Path

from scripts.manual_formal_decision_completion import build_formal_decision_completion_request


class FormalDecisionCompletionIngressTests(unittest.TestCase):
    def test_builder_uses_post_bds_state_sync_contract(self):
        payload = build_formal_decision_completion_request(
            {
                "request_id": "fd-parent",
                "requested_at_beijing": "2026-10-15T15:00:00+08:00",
                "market_date": "2026-10-15",
            },
            "requests/live_snapshot/fd-parent__business_decision_source.json",
            "fd-parent__decision",
            hashlib.sha256(b"FINAL_CONTENT").hexdigest(),
        )
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["source"], "CHATGPT_FORMAL_DECISION_COMPLETION")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_DECISION_COMPLETION")
        self.assertEqual(payload["parent_request_id"], "fd-parent")

    def test_bds_and_completion_are_distinct_in_canonical_owner(self):
        text = Path("scripts/process_state_sync_request.py").read_text(encoding="utf-8")
        self.assertIn("original_source_dispatch", text)
        self.assertIn("completion_dispatch", text)
        self.assertIn('"completion_required": True', text)
        self.assertIn("Formal Decision completion must reference BUSINESS_DECISION_SOURCE", text)


if __name__ == "__main__":
    unittest.main()
