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

    def test_canonical_report_builder_always_satisfies_validator_schema(self):
        request = center.build_report_delivery_request(
            task_id="ETF交易复盘", task_run_id="run-20260915-2030",
            report_id="report-20260915", report_type="ETF_TRADE_REVIEW",
            effective_market_date="2026-09-15",
            title="ETF交易复盘｜2026-09-15", summary="降级但正式完成",
            full_content="close evidence unavailable; no close inferred",
            source_reference="scheduled-trade-review:20260915",
            idempotency_key="ETF_TRADE_REVIEW:20260915:run-20260915-2030",
            generated_at="2026-09-15T20:30:00+08:00",
        )
        valid, reason = center.validate_report_delivery_request(request)
        self.assertTrue(valid, reason)
        for field in ("task_id", "task_run_id", "content_hash", "idempotency_key"):
            self.assertTrue(request[field])

    def test_canonical_report_builder_supports_system_review(self):
        request = center.build_report_delivery_request(
            task_id="ETF系统复核", task_run_id="run-20260920-0800",
            report_id="system-review-20260920-0800", report_type="ETF_SYSTEM_REVIEW",
            effective_market_date="2026-09-20",
            title="【系统复核】ETF系统复核｜2026-09-20 08:00", summary="系统复核完成",
            full_content="frozen system review body",
            source_reference="scheduled-system-review:20260920T0800",
            idempotency_key="ETF_SYSTEM_REVIEW:20260920:run-20260920-0800",
            generated_at="2026-09-20T08:00:00+08:00",
        )
        self.assertEqual(request["report_type"], "ETF_SYSTEM_REVIEW")
        valid, reason = center.validate_report_delivery_request(request)
        self.assertTrue(valid, reason)

    def test_canonical_report_builder_rejects_retired_report_type(self):
        with self.assertRaises(ValueError):
            center.build_report_delivery_request(
                task_id="ETF正式决策", task_run_id="run-retired",
                report_id="retired", report_type="ETF_FORMAL_DECISION",
                effective_market_date="2026-09-20",
                title="retired", summary="retired", full_content="retired",
                source_reference="retired", idempotency_key="retired",
                generated_at="2026-09-20T08:00:00+08:00",
            )

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
