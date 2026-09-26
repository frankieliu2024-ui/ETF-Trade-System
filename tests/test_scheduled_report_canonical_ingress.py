import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from notification_center import validate_report_delivery_request


def direct_scheduled_report(report_type: str = "ETF_TRADE_REVIEW") -> dict:
    """Model the external Scheduled Actor contract: no repo-local Python ingress."""
    body = "【测试报告】\n\n正文保持逐字一致。"
    return {
        "schema_version": "1.0",
        "channel": "REPORT",
        "report_type": report_type,
        "report_id": "report-20260926",
        "task_id": "ETF交易复盘" if report_type == "ETF_TRADE_REVIEW" else "ETF系统复核",
        "task_run_id": "run-20260926",
        "generated_at": "2026-09-26T11:30:00+08:00",
        "effective_market_date": "2026-09-25",
        "source_actor": "ChatGPT",
        "source_reference": "scheduled-review:run-20260926",
        "title": "【测试】",
        "summary": "测试",
        "full_content": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "idempotency_key": f"{report_type}:run-20260926",
        "delivery_mode": "FULL_REPORT",
        "no_trade_authority": True,
    }


class ScheduledReportDirectDeliveryContractTests(unittest.TestCase):
    def test_trade_review_direct_report_is_accepted_by_existing_consumer_validator(self):
        request = direct_scheduled_report("ETF_TRADE_REVIEW")
        self.assertEqual(validate_report_delivery_request(request), (True, ""))

    def test_system_review_direct_report_is_accepted_by_existing_consumer_validator(self):
        request = direct_scheduled_report("ETF_SYSTEM_REVIEW")
        self.assertEqual(validate_report_delivery_request(request), (True, ""))

    def test_direct_report_with_drifted_hash_fails_closed(self):
        request = direct_scheduled_report()
        request["content_hash"] = "not-the-frozen-body-hash"
        self.assertEqual(validate_report_delivery_request(request), (False, "content_hash_mismatch"))

    def test_retired_formal_decision_report_still_fails_closed(self):
        request = direct_scheduled_report("ETF_FORMAL_DECISION")
        self.assertEqual(validate_report_delivery_request(request), (False, "unsupported_report_type"))


if __name__ == "__main__":
    unittest.main()
