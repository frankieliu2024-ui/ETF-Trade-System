import hashlib
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from pathlib import Path
import base64
import json

from scripts import notification_center
from scripts import scheduled_review_completion_recovery as recovery


class FakeHandoffStore:
    def __init__(self):
        self.completion = None
        self.event = None
        self.report = None
        self.notification_id = None
        self.pushplus_calls = 0

    def write_completion(self, payload):
        if self.completion is not None and self.completion != payload:
            raise RuntimeError("completion identity conflict")
        self.completion = dict(payload)

    def publish_event(self):
        if self.completion is None:
            raise RuntimeError("completion is required")
        self.event = {
            "request_id": self.completion["request_id"],
            "task_run_id": self.completion["task_run_id"],
            "occurrence_id": self.completion["occurrence_id"],
            "content_hash": self.completion["content_hash"],
        }

    def project_report(self):
        if self.event is None:
            raise RuntimeError("event is required")
        self.report = notification_center.build_report_from_handoff({
            "schema_version": "1.0",
            "report_type": self.completion["report_type"],
            "task_id": self.completion["task_id"],
            "task_run_id": self.event["task_run_id"],
            "generated_at": "2026-10-25T20:00:00+08:00",
            "effective_market_date": "2026-10-23",
            "title": notification_center.canonical_report_title(self.completion["report_type"]),
            "summary": "simulated",
            "full_content": self.completion["full_content"],
        })

    def notify(self, fail_once=False):
        if self.report is None:
            raise RuntimeError("report is required")
        valid, reason = notification_center.validate_report_delivery_request(self.report)
        if not valid:
            raise RuntimeError(reason)
        if fail_once and self.pushplus_calls == 0:
            self.pushplus_calls += 1
            raise TimeoutError("simulated PushPlus timeout")
        self.pushplus_calls += 1
        self.notification_id = f"notification:{self.report['idempotency_key']}"
        return self.notification_id


def _completion(task_id, report_type, suffix):
    body = f"【{notification_center.canonical_report_title(report_type)}】2026-10-25 20:00 模拟验收"
    return {
        "request_id": f"sim-{suffix}",
        "task_id": task_id,
        "task_run_id": f"run-{suffix}",
        "occurrence_id": f"occ-{suffix}",
        "report_type": report_type,
        "full_content": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


class ThreeTaskNotificationReplayHarnessTests(unittest.TestCase):
    CASES = (
        ("ETF系统复核", "ETF_SYSTEM_REVIEW", "system"),
        ("ETF交易复盘", "ETF_TRADE_REVIEW", "trade"),
        ("ETF正式决策", "ETF_FORMAL_DECISION", "formal"),
    )

    def test_each_task_replays_completion_to_notification(self):
        for task_id, report_type, suffix in self.CASES:
            store = FakeHandoffStore()
            completion = _completion(task_id, report_type, suffix)
            store.write_completion(completion)
            store.publish_event()
            store.project_report()
            notification_id = store.notify()
            self.assertEqual(notification_id, f"notification:{report_type}:{completion['task_run_id']}")
            self.assertEqual(store.event["content_hash"], completion["content_hash"])
            self.assertEqual(store.report["full_content"], completion["full_content"])

    def test_pushplus_failure_recovers_without_second_chain(self):
        store = FakeHandoffStore()
        store.write_completion(_completion("ETF交易复盘", "ETF_TRADE_REVIEW", "retry"))
        store.publish_event()
        store.project_report()
        with self.assertRaises(TimeoutError):
            store.notify(fail_once=True)
        recovered_id = store.notify()
        self.assertEqual(recovered_id, "notification:ETF_TRADE_REVIEW:run-retry")
        self.assertEqual(store.pushplus_calls, 2)
        self.assertEqual(store.event["task_run_id"], "run-retry")

    def test_identity_conflict_is_rejected_before_event(self):
        store = FakeHandoffStore()
        store.write_completion(_completion("ETF正式决策", "ETF_FORMAL_DECISION", "conflict"))
        conflicting = _completion("ETF正式决策", "ETF_FORMAL_DECISION", "other")
        with self.assertRaisesRegex(RuntimeError, "identity conflict"):
            store.write_completion(conflicting)
        self.assertIsNone(store.event)

    def test_write_recovery_is_exercised_before_the_replay(self):
        target = Path(__file__).resolve().parent / "_simulated_completion.json"
        target.write_text('{"request_id":"sim-recovery","content_hash":"h"}', encoding="utf-8")
        try:
            calls = []
            def fake_open(req, timeout=30):
                calls.append(req.get_method())
                if req.get_method() == "GET" and calls.count("GET") == 1:
                    raise HTTPError(req.full_url, 404, "missing", {}, None)
                if req.get_method() == "PUT":
                    return _Response({}, 201)
                return _Response({"content": base64.b64encode(target.read_bytes()).decode("ascii")})
            with patch.dict(recovery.os.environ, {"GITHUB_TOKEN": "test", "GITHUB_REPOSITORY": "owner/repo"}),                  patch.object(recovery, "ROOT", target.parent),                  patch.object(recovery.urllib_request, "urlopen", side_effect=fake_open):
                _, receipt = recovery.persist_completion_with_recovery(target)
            self.assertTrue(receipt["read_back"])
        finally:
            target.unlink(missing_ok=True)


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
