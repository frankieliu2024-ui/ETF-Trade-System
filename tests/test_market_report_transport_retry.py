from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import notification_center
import run_guarded_notification as guarded


class ReportTransportRetryTests(unittest.TestCase):
    def _center_with_state(self, directory: str, response: dict, source: str = "report-delivery:test"):
        state_path = Path(directory) / "notification_center.json"
        state_path.write_text(json.dumps({
            "notifications": [{
                "source_event_id": source,
                "event_type": "REPORT_DELIVERY_REQUEST",
                "lifecycle_status": "FAILED",
                "last_attempted_at": "2026-09-14T09:30:28+08:00",
                "response": response,
            }]
        }), encoding="utf-8")
        return SimpleNamespace(STATE=Path(directory), read_json=notification_center.read_json)

    def test_tls_handshake_timeout_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            center = self._center_with_state(directory, {
                "error": "URLError",
                "message": "<urlopen error _ssl.c:999: The handshake operation timed out>",
            })
            self.assertTrue(guarded._report_retryable_transport_failure(
                center, {"source_event_id": "report-delivery:test"}
            ))

    def test_gateway_failures_are_retryable(self):
        for status in (502, 503, 504):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                center = self._center_with_state(directory, {
                    "error": "HTTPError",
                    "message": f"HTTP Error {status}: Bad Gateway",
                })
                self.assertTrue(guarded._report_retryable_transport_failure(
                    center, {"source_event_id": "report-delivery:test"}
                ))

    def test_unknown_or_ambiguous_failures_are_not_retryable(self):
        cases = [
            {"error": "TimeoutError", "message": "timed out while reading response"},
            {"error": "URLError", "message": "<urlopen error [Errno -2] Name or service not known>"},
            {"error": "HTTPError", "message": "HTTP Error 401: Unauthorized"},
            {"error": "HTTPError", "message": "HTTP Error 429: Too Many Requests"},
        ]
        for response in cases:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                center = self._center_with_state(directory, response)
                self.assertFalse(guarded._report_retryable_transport_failure(
                    center, {"source_event_id": "report-delivery:test"}
                ))

    def test_wrong_report_identity_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            center = self._center_with_state(directory, {
                "error": "HTTPError",
                "message": "HTTP Error 502: Bad Gateway",
            })
            self.assertFalse(guarded._report_retryable_transport_failure(
                center, {"source_event_id": "report-delivery:other"}
            ))

    def test_report_center_retries_with_fixed_bound_and_preserves_event(self):
        event = {
            "source_event_id": "report-delivery:test",
            "event_type": "REPORT_DELIVERY_REQUEST",
            "title": "report",
        }
        with patch.object(notification_center, "choose_event", return_value=event), \
             patch.object(notification_center, "main", side_effect=[1, 1, 0]) as main_mock, \
             patch.object(guarded, "_report_retryable_transport_failure", return_value=True) as retry_mock, \
             patch.object(guarded.time, "sleep") as sleep_mock, \
             patch.object(guarded, "notification_evidence_error", return_value=None):
            result = guarded._run_center("event")
        self.assertEqual(result, 0)
        self.assertEqual(main_mock.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep_mock.call_args_list], [2.0, 8.0])
        self.assertEqual(retry_mock.call_count, 2)
        self.assertTrue(all(call.args[1] is event for call in retry_mock.call_args_list))

    def test_report_center_stops_after_bounded_persistent_failure(self):
        event = {
            "source_event_id": "report-delivery:test",
            "event_type": "REPORT_DELIVERY_REQUEST",
            "title": "report",
        }
        with patch.object(notification_center, "choose_event", return_value=event), \
             patch.object(notification_center, "main", return_value=1) as main_mock, \
             patch.object(guarded, "_report_retryable_transport_failure", return_value=True), \
             patch.object(guarded.time, "sleep"), \
             patch.object(guarded, "notification_evidence_error", return_value=None):
            result = guarded._run_center("event")
        self.assertEqual(result, 1)
        self.assertEqual(main_mock.call_count, 3)

    def test_non_report_center_does_not_retry(self):
        event = {"source_event_id": "system:test", "event_type": "SYSTEM_EVENT", "title": "system"}
        with patch.object(notification_center, "choose_event", return_value=event), \
             patch.object(notification_center, "main", return_value=1) as main_mock, \
             patch.object(guarded, "_report_retryable_transport_failure", return_value=True), \
             patch.object(guarded.time, "sleep"), \
             patch.object(guarded, "notification_evidence_error", return_value=None):
            result = guarded._run_center("event")
        self.assertEqual(result, 1)
        self.assertEqual(main_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
