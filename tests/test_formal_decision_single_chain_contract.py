import hashlib
import unittest
from datetime import datetime, timezone, timedelta

from scripts.create_scheduled_formal_request import build_scheduled_request
from scripts.formal_entry_contract import validate_canonical_formal_request


class FormalDecisionSingleChainContractTests(unittest.TestCase):
    def _request(self, mode, source, prefix):
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
        body = "【ETF正式决策】2026-10-16 12:00 正式判断\n\n【业务结论】\n完整正文"
        payload["presentation_binding"].update(
            full_content=body,
            content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )
        return payload

    def test_all_three_entries_share_one_canonical_request_contract(self):
        cases = (
            ("MANUAL_CONVERSATION", "CHATGPT_MANUAL_FORMAL_ANALYSIS", "conversation"),
            ("MANUAL_SCHEDULED_TASK", "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", "manual-scheduled"),
            ("AUTOMATIC_SCHEDULED_TASK", "CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "scheduled_"),
        )
        for mode, source, prefix in cases:
            valid, reason = validate_canonical_formal_request(self._request(mode, source, prefix))
            self.assertTrue(valid, (mode, reason))

    def test_legacy_formal_envelopes_are_not_canonical_ingress(self):
        payload = self._request(
            "MANUAL_CONVERSATION", "CHATGPT_MANUAL_FORMAL_ANALYSIS", "conversation"
        )
        payload["request_type"] = "MARKET_QUOTE_REFRESH"
        valid, reason = validate_canonical_formal_request(payload)
        self.assertFalse(valid)
        self.assertEqual(reason, "formal_request_type_must_be_STATE_SYNC_ONLY")

    def test_binding_mismatch_fails_closed(self):
        payload = self._request(
            "MANUAL_CONVERSATION", "CHATGPT_MANUAL_FORMAL_ANALYSIS", "conversation"
        )
        payload["presentation_binding"]["task_run_id"] = "other-run"
        valid, reason = validate_canonical_formal_request(payload)
        self.assertFalse(valid)
        self.assertEqual(reason, "presentation_binding_task_run_id_mismatch")


if __name__ == "__main__":
    unittest.main()
