import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts.market_quote_router import evaluate_interactive_decision_freshness
from scripts import market_notification_common as notifications


class CurrentDecisionFreshnessTests(unittest.TestCase):
    NOW = datetime.fromisoformat("2026-08-31T10:50:00+08:00")
    POLICY = {"interactive_decision_freshness": {"preferred_max_age_seconds": 300, "fallback_max_age_seconds": 900}}

    def test_pass_current_at_1043_requires_refresh(self):
        current = {"captured_at": "2026-08-31T10:43:20+08:00", "provider_as_of": "2026-08-31T10:43:18+08:00", "quality_status": "PASS"}
        gate = evaluate_interactive_decision_freshness(current, self.NOW, self.NOW, self.POLICY)
        self.assertEqual(gate["status"], "DEGRADED")
        self.assertFalse(gate["formal_decision_allowed"])

    def test_pass_current_at_1049_is_consumable(self):
        current = {"captured_at": "2026-08-31T10:49:20+08:00", "provider_as_of": "2026-08-31T10:49:18+08:00", "quality_status": "PASS"}
        gate = evaluate_interactive_decision_freshness(current, self.NOW, self.NOW, self.POLICY)
        self.assertEqual(gate["status"], "DEGRADED")
        self.assertTrue(gate["fallback_allowed"])
        self.assertFalse(gate["formal_decision_allowed"])

    def test_refresh_failure_cannot_silently_use_pre_request_current(self):
        current = {"captured_at": "2026-08-31T10:43:20+08:00", "provider_as_of": "2026-08-31T10:43:18+08:00", "quality_status": "PASS"}
        gate = evaluate_interactive_decision_freshness(current, self.NOW, self.NOW, self.POLICY)
        self.assertTrue(gate["fallback_allowed"])
        self.assertFalse(gate["formal_decision_allowed"])
        self.assertTrue(gate["refresh_required"])

    def test_pre_request_fresh_current_still_requires_refresh_attempt(self):
        current = {"captured_at": "2026-08-31T10:49:20+08:00", "provider_as_of": "2026-08-31T10:49:18+08:00", "quality_status": "PASS"}
        gate = evaluate_interactive_decision_freshness(current, self.NOW, self.NOW, self.POLICY)
        self.assertFalse(gate["formal_decision_allowed"])
        self.assertTrue(gate["refresh_required"])


class PushPlusNotificationClosureTests(unittest.TestCase):
    def test_missing_token_is_persisted_as_failure(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            state_file = state_dir / "notification_center.json"
            with patch.object(notifications, "NOTIFICATION_STATE", state_file), patch.object(notifications, "STATE", state_dir), patch.dict(os.environ, {}, clear=True):
                event = {"key": "event-1", "event_type": "SYSTEM_EVENT", "title": "系统异常", "content": "失败", "source": "test"}
                first = notifications.persist_and_send(event, policy="test")
                self.assertEqual(first["status"], "FAILED")
                saved = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["notifications"][0]["lifecycle_status"], "FAILED")
                second = notifications.persist_and_send(event, policy="test")
                self.assertEqual(second["status"], "FAILED")
                saved = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertEqual(len([x for x in saved["notifications"] if x["notification_id"] == first["notification_id"]]), 1)

    def test_successful_duplicate_is_deduped(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "notification_center.json"
            event = {"key": "event-duplicate", "event_type": "SYSTEM_EVENT", "title": "系统异常", "content": "失败", "source": "test"}
            with patch.object(notifications, "NOTIFICATION_STATE", state_file), patch.object(notifications, "STATE", Path(td)), patch.dict(os.environ, {"PUSHPLUS_TOKEN": "token"}), patch.object(notifications, "send", return_value=(True, {"pushplus_code": 200})) as sender:
                self.assertEqual(notifications.persist_and_send(event, policy="test")["status"], "SENT")
                self.assertEqual(notifications.persist_and_send(event, policy="test")["status"], "ALREADY_MANAGED")
            sender.assert_called_once()

    def test_pushplus_failure_is_persisted_with_response(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "notification_center.json"
            event = {"key": "event-2", "event_type": "SYSTEM_EVENT", "title": "系统异常", "content": "失败", "source": "test"}
            with patch.object(notifications, "NOTIFICATION_STATE", state_file), patch.object(notifications, "STATE", Path(td)), patch.dict(os.environ, {"PUSHPLUS_TOKEN": "token"}), patch.object(notifications, "send", return_value=(False, {"error": "timeout"})):
                result = notifications.persist_and_send(event, policy="test")
            self.assertEqual(result["status"], "FAILED")
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["notifications"][0]["response"]["error"], "timeout")

    def test_notification_center_main_persists_missing_token(self):
        with tempfile.TemporaryDirectory() as td:
            import scripts.notification_center as center
            state_file = Path(td) / "notification_center.json"
            event = {"key": "center-event", "event_type": "SYSTEM_EVENT", "title": "系统异常", "content": "失败", "source": "test"}
            with patch.object(center, "STATE", Path(td)), patch.dict(os.environ, {}, clear=True), patch.object(center, "choose_event", return_value=event):
                with patch("sys.argv", ["notification_center.py", "--mode", "event"]):
                    self.assertEqual(center.main(), 1)
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["notifications"][0]["lifecycle_status"], "FAILED")


if __name__ == "__main__":
    unittest.main()

