"""System Review notification workflow and presentation contract tests."""
import hashlib
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.scheduled_review_completion import validate_system_review_user_facing_clarity

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "run_scheduled_system_review_actor.py"


def _load():
    spec = importlib.util.spec_from_file_location("system_review_actor_run_guard_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, runs):
        import json
        self.body = json.dumps({"workflow_runs": runs}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class SystemReviewNotificationRunReadbackTests(unittest.TestCase):
    def _projection_fixture(self, *, report_content=None):
        content = "【ETF系统复核】\n本次复核正文"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        completion = {"request_id": "req-1", "presentation_binding": {
            "task_id": "ETF系统复核", "task_run_id": "run-1", "occurrence_id": "occ-1",
            "request_id": "req-1", "report_id": "etf_system_review:run-1",
            "report_type": "ETF_SYSTEM_REVIEW", "full_content": content, "content_hash": digest,
        }}
        event = {"event_type": "FORMAL_SCHEDULED_SYSTEM_REVIEW", "request_id": "req-1",
                 "presentation_binding": dict(completion["presentation_binding"])}
        report = {"report_type": "ETF_SYSTEM_REVIEW", "report_id": "etf_system_review:run-1",
                  "request_id": "req-1", "task_id": "ETF系统复核", "task_run_id": "run-1",
                  "title": "【ETF系统复核】", "full_content": report_content or content,
                  "content_hash": digest, "delivery_mode": "FULL_REPORT", "no_trade_authority": True}
        notification = {"notification_id": "nid-1", "report_id": "etf_system_review:run-1",
                        "request_id": "req-1", "event_type": "REPORT_DELIVERY_REQUEST",
                        "notification_channel": "REPORT", "delivery_mode": "FULL_REPORT",
                        "title": "【ETF系统复核】", "content": content, "content_hash": digest,
                        "lifecycle_status": "SENT",
                        "response": {"http_status": 200, "pushplus_code": 200}}
        files = {
            "events/reviews/system_run-1.json": event,
            "requests/report_delivery/etf_system_review__run-1.json": report,
            "data/state/notification_center.json": {"notifications": [notification]},
        }
        return content, completion, files

    def test_system_projection_requires_exact_event_report_and_provider_record(self):
        actor = _load()
        _, completion, files = self._projection_fixture()
        with patch.object(actor, "read_main_json", side_effect=lambda path: files.get(path)):
            result = actor._wait_for_system_projection(completion)
        self.assertTrue(result["verified"])
        self.assertEqual(result["notification_id"], "nid-1")

    def test_system_projection_rejects_provider_success_without_exact_full_content(self):
        actor = _load()
        _, completion, files = self._projection_fixture(report_content="截断正文")
        with patch.object(actor, "read_main_json", side_effect=lambda path: files.get(path)), \
             patch.object(actor.time, "time", side_effect=[100, 100, 221]), \
             patch.object(actor.time, "sleep"):
            result = actor._wait_for_system_projection(completion)
        self.assertFalse(result["verified"])
        self.assertFalse(result["checks"]["report_content"])

    def test_exact_completion_run_accepted(self):
        actor = _load()
        path = "requests/live_snapshot/unique__scheduled_review_completion.json"
        run = {"id": 123, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch",
               "head_branch": "main", "display_title": "ETF decision notification — " + path,
               "status": "completed", "conclusion": "success"}
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), \
             patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])):
            result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
        self.assertTrue(result["verified"])
        self.assertEqual(result["run_id"], 123)

    def test_other_completion_run_not_accepted(self):
        actor = _load()
        path = "requests/live_snapshot/unique__scheduled_review_completion.json"
        run = {"id": 124, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch",
               "head_branch": "main", "display_title": "ETF decision notification — different"}
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), \
             patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])), \
             patch.object(actor.time, "monotonic", side_effect=[0, 61]):
            result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
        self.assertFalse(result["verified"])

    def test_missing_credentials_fail_closed(self):
        actor = _load()
        with patch.dict("os.environ", {"GITHUB_TOKEN": "", "ETF_GITHUB_PAT": "", "GH_TOKEN": ""}):
            result = actor._readback_notification_run("path", datetime.now(timezone.utc))
        self.assertFalse(result["verified"])

    def test_matching_but_in_progress_run_is_not_success(self):
        actor = _load()
        path = "requests/live_snapshot/unique__scheduled_review_completion.json"
        run = {"id": 125, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch",
               "head_branch": "main", "display_title": "ETF decision notification — " + path,
               "status": "in_progress", "conclusion": None}
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), \
             patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])), \
             patch.object(actor.time, "monotonic", side_effect=[0, 0, 61]), \
             patch.object(actor.time, "sleep"):
            result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
        self.assertFalse(result["verified"])

    def test_matching_failed_run_is_not_success(self):
        actor = _load()
        path = "requests/live_snapshot/unique__scheduled_review_completion.json"
        run = {"id": 126, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch",
               "head_branch": "main", "display_title": "ETF decision notification — " + path,
               "status": "completed", "conclusion": "failure"}
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), \
             patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])):
            result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
        self.assertFalse(result["verified"])
        self.assertEqual(result["conclusion"], "failure")

    def test_user_facing_guard_rejects_internal_identifiers(self):
        for content in (
            "本次运行 request_id=internal-123，已完成。",
            "平台未提供 task_run_id: unknown。",
            "内部状态 CURRENT.close_review_closure_is_stale。",
            "入口为 MANUAL_CONVERSATION。",
        ):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "internal audit identifiers"):
                validate_system_review_user_facing_clarity(content)

    def test_user_facing_guard_allows_readable_operational_facts(self):
        for content in (
            "【晚间复核结论】系统一致性检查通过，暂无硬错误。",
            "【未闭环事项】收盘复核记录尚未同步到最新交易日，自动恢复受限。",
            "【通知与记录状态】本次复核记录已保存并核实，PushPlus通知已投递。",
            "【关键事实】ETF持仓和Discovery观察层身份保持独立。",
        ):
            with self.subTest(content=content):
                validate_system_review_user_facing_clarity(content)


if __name__ == "__main__":
    unittest.main()
