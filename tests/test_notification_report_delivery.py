import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import notification_center


class ReportDeliveryContractTests(unittest.TestCase):
    def _request(self, report_type="ETF_TRADE_REVIEW"):
        body = f"# {report_type}\\n\\n已完成的正式报告。"
        return {
            "schema_version": "1.0", "channel": "REPORT", "report_type": report_type,
            "report_id": f"report-{report_type.lower()}-20260903",
            "task_id": report_type.lower(), "task_run_id": f"run-{report_type.lower()}-20260903",
            "generated_at": "2026-09-03T22:00:00+08:00", "effective_market_date": "2026-09-03",
            "source_actor": "ChatGPT", "source_reference": f"product-task:{report_type}",
            "title": f"【正式报告】{report_type}｜2026-09-03", "summary": "正式报告已完成",
            "full_content": body, "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "idempotency_key": f"{report_type}:2026-09-03:report-{report_type.lower()}",
            "delivery_mode": "FULL_REPORT", "no_trade_authority": True,
        }

    def test_all_three_report_types_are_shared_delivery_events(self):
        for report_type in ("ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW", "ETF_FORMAL_DECISION"):
            request = self._request(report_type)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
                with patch.object(notification_center, "REPORT_REQUEST_DIR", Path(directory)):
                    valid, reason = notification_center.validate_report_delivery_request(request)
                    event = notification_center.report_delivery_event()
            self.assertTrue(valid, (report_type, reason))
            self.assertEqual(event["report_type"], report_type)
            self.assertEqual(event["notification_channel"], "REPORT")
            self.assertEqual(event["delivery_mode"], "FULL_REPORT")
            self.assertEqual(event["content"], request["full_content"])
            self.assertTrue(event["no_trade_authority"])

    def test_unknown_report_type_is_rejected(self):
        request = self._request("ETF_UNKNOWN_REPORT")
        self.assertFalse(notification_center.validate_report_delivery_request(request)[0])

    def test_invalid_report_is_rejected(self):
        request = self._request()
        request["content_hash"] = "wrong"
        self.assertFalse(notification_center.validate_report_delivery_request(request)[0])
        request = self._request()
        request["no_trade_authority"] = False
        self.assertFalse(notification_center.validate_report_delivery_request(request)[0])

    def test_report_is_terminal_and_existing_events_remain_interrupt_compact(self):
        report = notification_center.report_delivery_event
        self.assertIsNotNone(report)
        item = notification_center.normalize_notification(
            {"key": "market:1", "event_type": "SYSTEM_EVENT", "title": "test", "content": "test"}
        )
        self.assertEqual(item["notification_channel"], "INTERRUPT")
        self.assertEqual(item["delivery_mode"], "COMPACT")
        self.assertNotEqual(item["notification_channel"], "REPORT")


if __name__ == "__main__":
    unittest.main()
