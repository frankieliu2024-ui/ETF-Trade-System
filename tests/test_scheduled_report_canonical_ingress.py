import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from notification_center import validate_report_delivery_request
from write_report_delivery_request import build_and_write_report_request


class ScheduledReportCanonicalIngressTests(unittest.TestCase):
    def _write(self, report_type: str):
        body = "【测试报告】\n\n正文保持逐字一致。"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            request = build_and_write_report_request(
                output=path,
                task_id="ETF交易复盘" if report_type == "ETF_TRADE_REVIEW" else "ETF系统复核",
                task_run_id="run-20260923",
                report_id="report-20260923",
                report_type=report_type,
                effective_market_date="2026-09-23",
                title="【测试】",
                summary="测试",
                full_content=body,
                source_reference="scheduled-review:test",
                idempotency_key=f"{report_type}:test",
                generated_at="2026-09-23T20:30:00+08:00",
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
        return body, request, persisted

    def test_trade_review_uses_validator_complete_canonical_schema(self):
        body, request, persisted = self._write("ETF_TRADE_REVIEW")
        self.assertEqual(request, persisted)
        self.assertEqual(persisted["full_content"], body)
        self.assertEqual(persisted["content_hash"], hashlib.sha256(body.encode("utf-8")).hexdigest())
        self.assertEqual(persisted["channel"], "REPORT")
        self.assertEqual(persisted["generated_at"], "2026-09-23T20:30:00+08:00")
        self.assertEqual(persisted["effective_market_date"], "2026-09-23")
        self.assertEqual(persisted["source_actor"], "ChatGPT")
        self.assertEqual(validate_report_delivery_request(persisted), (True, ""))

    def test_system_review_uses_same_canonical_ingress(self):
        body, _, persisted = self._write("ETF_SYSTEM_REVIEW")
        self.assertEqual(persisted["full_content"], body)
        self.assertEqual(validate_report_delivery_request(persisted), (True, ""))


if __name__ == "__main__":
    unittest.main()
