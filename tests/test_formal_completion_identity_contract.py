import hashlib
import unittest
from unittest.mock import patch

from scripts.scheduled_review_completion import build_formal_review_completion_request


class FormalCompletionIdentityContractTests(unittest.TestCase):
    def test_shared_completion_contains_decision_identity_at_both_levels(self):
        content = "【ETF正式决策】2026-10-26 14:48 正式判断\n\n正文"
        review = {
            "review_scope": "FULL_DAY",
            "market_date": "2026-10-26",
            "review_version": "FULL_DAY_REVIEW",
            "reviewed_at_beijing": "2026-10-26 14:48",
            "risk_permission": "允许",
            "opportunity_status": "无",
            "main_candidate": "现金",
            "lifecycle": {},
            "holding_actions": {},
            "amount_yuan": 0,
            "action": "不变",
            "data_time": {"close_snapshot": "2026-10-26 14:45"},
            "account_close": {},
            "capital_efficiency": "现金",
            "capital_efficiency_reason": "测试",
            "next_unit_capital_use": "现金",
            "max_risk": "测试",
            "error_reason": "测试",
            "information_gap": "无",
            "most_fragile_hypothesis": "无",
            "overseas_overestimate": "无",
            "overseas_a_share_divergence": "无",
            "case_mode": "FULL_DAY_REVIEW",
            "review_boundary": "2026-10-26",
            "fact_binding": {
                "request_id": "req-current",
                "market_snapshot_status": "READY",
                "fact_chain_status": "READY",
                "query_context_status": "READY",
                "decision_context_status": "READY",
                "objects_status": "READY",
                "gate_status": "READY",
            },
        }
        with patch("scripts.scheduled_review_completion.validate_user_visible_content"), \
             patch("scripts.scheduled_review_completion.validate_formal_decision_presentation_timestamp"):
            payload = build_formal_review_completion_request(
                review, "req-current", "2026-10-26T14:48:00+08:00",
                market_date="2026-10-26", parent_request_id="parent-current",
                final_content=content, task_run_id="run-current",
                occurrence_id="occ-current", entry_mode="MANUAL_CONVERSATION",
            )
        binding = payload["presentation_binding"]
        self.assertEqual(payload["parent_request_id"], "parent-current")
        self.assertEqual(binding["parent_request_id"], "parent-current")
        self.assertEqual(payload["decision_id"], "ETF_FORMAL_DECISION:run-current")
        self.assertEqual(binding["decision_id"], payload["decision_id"])
        self.assertEqual(binding["report_id"], payload["decision_id"])
        self.assertEqual(binding["content_hash"], hashlib.sha256(content.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
