from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import notification_center as center


class NotificationActiveReportContractTests(unittest.TestCase):
    def _request(self, report_type: str) -> dict:
        body = f"full report for {report_type}"
        return {
            "schema_version": "1.0",
            "channel": "REPORT",
            "report_type": report_type,
            "report_id": f"report-{report_type}",
            "task_id": report_type,
            "task_run_id": f"run-{report_type}",
            "generated_at": "2026-09-15T08:00:00+08:00",
            "effective_market_date": "2026-09-15",
            "source_actor": "ChatGPT",
            "source_reference": "product-task:test",
            "title": f"title-{report_type}",
            "summary": "summary",
            "full_content": body,
            "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "idempotency_key": f"idem-{report_type}",
            "delivery_mode": "FULL_REPORT",
            "no_trade_authority": True,
        }

    def test_only_active_scheduled_report_types_are_accepted(self):
        for report_type in ("ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW"):
            valid, reason = center.validate_report_delivery_request(self._request(report_type))
            self.assertTrue(valid, reason)

    def test_retired_scheduled_formal_decision_report_is_rejected_for_new_delivery(self):
        valid, reason = center.validate_report_delivery_request(self._request("ETF_FORMAL_DECISION"))
        self.assertFalse(valid)
        self.assertEqual(reason, "unsupported_report_type")
        self.assertIn("ETF_FORMAL_DECISION", center.HISTORICAL_REPORT_TYPES)
        self.assertNotIn("ETF_FORMAL_DECISION", center.ACTIVE_REPORT_TYPES)

    def test_historical_formal_report_notification_record_remains_readable(self):
        historical = {
            "notification_id": "historical-formal-report",
            "event_type": "REPORT_DELIVERY_REQUEST",
            "notification_channel": "REPORT",
            "delivery_mode": "FULL_REPORT",
            "source_event_id": "report-delivery:legacy-formal",
            "lifecycle_status": "SENT",
            "title": "legacy formal report",
            "content": "legacy content",
            "source": "legacy",
            "created_at": "2026-09-01T12:00:00+08:00",
            "sent_at": "2026-09-01T12:00:01+08:00",
            "confirmation_context": {},
        }
        normalized = center.normalize_notification(historical, historical)
        self.assertEqual(normalized["notification_channel"], "REPORT")
        self.assertEqual(normalized["lifecycle_status"], "SENT")
        self.assertEqual(normalized["title"], "legacy formal report")

    def test_manual_formal_decision_material_change_remains_interrupt_family(self):
        event = {
            "event_type": "FORMAL_DECISION_MATERIAL_CHANGE",
            "confirmation_context": {
                "opportunity_status": "Trial机会",
                "previous_opportunity_status": "观察机会",
                "risk_permission": "允许Trial",
                "previous_risk_permission": "允许Trial",
            },
        }
        self.assertEqual(center.canonical_template_family(event), "Trial机会")


if __name__ == "__main__":
    unittest.main()
