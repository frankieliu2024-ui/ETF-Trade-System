from __future__ import annotations

import unittest

from scripts.manual_formal_decision_identity import canonical_manual_identity


class ManualFormalDecisionIdentityTests(unittest.TestCase):
    def test_manual_completion_uses_parent_request_identity(self):
        decision = {
            "decision_id": "20260915_1447_manual_intraday_chat_decision",
            "opportunity_status": "观察机会",
            "risk_permission": "禁止新增",
            "main_candidate": "半导体设备ETF（561980）",
        }
        first = {
            "request_id": "20260915_1447_manual_intraday_chat__formal_completion",
            "parent_request_id": "20260915_1447_manual_intraday_chat",
            "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
        }
        retry = {
            "request_id": "20260915_1447_manual_intraday_chat__formal_completion_retry",
            "parent_request_id": "20260915_1447_manual_intraday_chat",
            "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
        }
        _, parent_first, fingerprint_first = canonical_manual_identity(first, decision, "2026-09-15")
        _, parent_retry, fingerprint_retry = canonical_manual_identity(retry, decision, "2026-09-15")
        self.assertEqual(parent_first, "20260915_1447_manual_intraday_chat")
        self.assertEqual(parent_retry, parent_first)
        self.assertEqual(fingerprint_retry, fingerprint_first)

    def test_non_manual_path_keeps_request_identity(self):
        decision = {"opportunity_status": "无机会"}
        request = {"request_id": "scheduled-legacy", "source": "OTHER"}
        request_id, parent_request_id, fingerprint = canonical_manual_identity(request, decision, "2026-09-15")
        self.assertEqual(request_id, "scheduled-legacy")
        self.assertEqual(parent_request_id, "")
        self.assertTrue(fingerprint)


if __name__ == "__main__":
    unittest.main()
