"""System Review notification workflow run identity tests (no external calls)."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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


def test_exact_completion_run_accepted():
    actor = _load()
    path = "requests/live_snapshot/unique__scheduled_review_completion.json"
    run = {"id": 123, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch", "head_branch": "main", "display_title": "ETF decision notification — " + path, "status": "completed", "conclusion": "success"}
    with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])):
        result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
    assert result["verified"] is True
    assert result["run_id"] == 123


def test_other_completion_run_not_accepted():
    actor = _load()
    path = "requests/live_snapshot/unique__scheduled_review_completion.json"
    run = {"id": 124, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch", "head_branch": "main", "display_title": "ETF decision notification — different"}
    with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])), patch.object(actor.time, "monotonic", side_effect=[0, 61]):
        result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
    assert result["verified"] is False


def test_missing_credentials_fail_closed():
    actor = _load()
    with patch.dict("os.environ", {"GITHUB_TOKEN": "", "ETF_GITHUB_PAT": "", "GH_TOKEN": ""}):
        result = actor._readback_notification_run("path", datetime.now(timezone.utc))
    assert result["verified"] is False


def test_matching_but_in_progress_run_is_not_success():
    actor = _load()
    path = "requests/live_snapshot/unique__scheduled_review_completion.json"
    run = {"id": 125, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch", "head_branch": "main", "display_title": "ETF decision notification — " + path, "status": "in_progress", "conclusion": None}
    with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])), patch.object(actor.time, "monotonic", side_effect=[0, 0, 61]), patch.object(actor.time, "sleep"):
        result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
    assert result["verified"] is False


def test_matching_failed_run_is_not_success():
    actor = _load()
    path = "requests/live_snapshot/unique__scheduled_review_completion.json"
    run = {"id": 126, "created_at": "2026-10-30T01:00:01Z", "event": "workflow_dispatch", "head_branch": "main", "display_title": "ETF decision notification — " + path, "status": "completed", "conclusion": "failure"}
    with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}), patch.object(actor.urllib_request, "urlopen", return_value=FakeResponse([run])):
        result = actor._readback_notification_run(path, datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
    assert result["verified"] is False
    assert result["conclusion"] == "failure"
