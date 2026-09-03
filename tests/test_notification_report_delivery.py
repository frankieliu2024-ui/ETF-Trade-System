import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import notification_center


class ReportDeliveryContractTests(unittest.TestCase):
    def _request(self):
        body = "# ETF日复盘\n\n已完成的正式报告。"
        return {
            "schema_version": "1.0",
            "channel": "REPORT",
            "report_type": "ETF_TRADE_REVIEW",
            "report_id": "review-20260903",
            "task_id": "etf-trade-review",
            "task_run_id": "run-20260903",
            "generated_at": "2026-09-03T22:00:00+08:00",
            "effective_market_date": "2026-09-03",
            "source_actor": "ChatGPT",
            "source_reference": "product-task:ETF交易复盘",
            "title": "【交易复盘】ETF日复盘｜2026-09-03",
            "summary": "正式收盘复盘已完成",
            "full_content": body,
            "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "idempotency_key": "ETF_TRADE_REVIEW:2026-09-03:review-20260903",
            "delivery_mode": "FULL_REPORT",
            "no_trade_authority": True,
        }

    def test_valid_report_is_shared_delivery_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            path.write_text(json.dumps(self._request(), ensure_ascii=False), encoding="utf-8")
            with patch.object(notification_center, "REPORT_REQUEST_DIR", Path(directory)):
                valid, reason = notification_center.validate_report_delivery_request(self._request())
                event = notification_center.report_delivery_event()
        self.assertTrue(valid, reason)
        self.assertEqual(event["notification_channel"], "REPORT")
        self.assertEqual(event["delivery_mode"], "FULL_REPORT")
        self.assertTrue(event["no_trade_authority"])

    def test_invalid_hash_or_trade_authority_is_rejected(self):
        request = self._request()
        request["content_hash"] = "wrong"
        self.assertEqual(notification_center.validate_report_delivery_request(request)[0], False)
        request = self._request()
        request["no_trade_authority"] = False
        self.assertEqual(notification_center.validate_report_delivery_request(request)[0], False)

    def test_existing_events_default_to_interrupt_compact(self):
        item = notification_center.normalize_notification({
            "key": "market:1", "event_type": "SYSTEM_EVENT",
            "title": "test", "content": "test"
        })
        self.assertEqual(item["notification_channel"], "INTERRUPT")
        self.assertEqual(item["delivery_mode"], "COMPACT")


if __name__ == "__main__":
    unittest.main()
