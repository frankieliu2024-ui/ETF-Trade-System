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
    def test_notification_state_merge_preserves_concurrent_runner_events(self):
        from scripts.merge_notification_state import merge_notification_state
        base = {"notifications": [{"notification_id": "a", "source_event_id": "a", "lifecycle_status": "SENT"}]}
        incoming = {"notifications": [{"notification_id": "b", "source_event_id": "b", "lifecycle_status": "SENT"}]}
        merged = merge_notification_state(base, incoming)
        self.assertEqual({x["notification_id"] for x in merged["notifications"]}, {"a", "b"})

    def test_notification_state_merge_preserves_newer_sent_state(self):
        from scripts.merge_notification_state import merge_notification_state
        base = {"updated_at": "2026-09-01T10:05:00+08:00", "notifications": [{"notification_id": "a", "source_event_id": "fact", "lifecycle_status": "SENT", "sent_at": "2026-09-01T10:05:00+08:00", "response": {"pushplus_code": 200}}]}
        incoming = {"updated_at": "2026-09-01T10:01:00+08:00", "notifications": [{"notification_id": "a", "source_event_id": "fact", "lifecycle_status": "FAILED", "last_attempted_at": "2026-09-01T10:01:00+08:00"}]}
        merged = merge_notification_state(base, incoming)
        self.assertEqual(len(merged["notifications"]), 1)
        self.assertEqual(merged["notifications"][0]["lifecycle_status"], "SENT")
        self.assertEqual(merged["notifications"][0]["response"]["pushplus_code"], 200)

    def test_notification_state_merge_accepts_newer_successful_attempt(self):
        from scripts.merge_notification_state import merge_notification_state
        base = {"notifications": [{"notification_id": "a", "source_event_id": "fact", "lifecycle_status": "CREATED", "created_at": "2026-09-01T10:01:00+08:00"}]}
        incoming = {"notifications": [{"notification_id": "a", "source_event_id": "fact", "lifecycle_status": "SENT", "last_attempted_at": "2026-09-01T10:05:00+08:00", "sent_at": "2026-09-01T10:05:00+08:00"}]}
        merged = merge_notification_state(base, incoming)
        self.assertEqual(merged["notifications"][0]["lifecycle_status"], "SENT")

    def test_notification_state_merge_deduplicates_shared_source_fact(self):
        from scripts.merge_notification_state import merge_notification_state
        base = {"notifications": [{"notification_id": "runner-a", "source_event_id": "fact-1", "key": "old-key", "lifecycle_status": "SENT"}]}
        incoming = {"notifications": [{"notification_id": "runner-b", "source_event_id": "fact-1", "key": "new-key", "lifecycle_status": "CREATED"}]}
        merged = merge_notification_state(base, incoming)
        self.assertEqual(len(merged["notifications"]), 1)
        self.assertEqual(merged["notifications"][0]["lifecycle_status"], "SENT")

    def test_hstech_rerun_keeps_one_sent_fact_identity(self):
        from scripts.merge_notification_state import merge_notification_state
        fact = "market-value:asia:2026-08-31:HSTECH:UP:REVERSAL:2026-08-31T16:09:08+08:00"
        merged = merge_notification_state(
            {"notifications": [{"notification_id": "stable", "source_event_id": fact, "lifecycle_status": "SENT", "sent_at": "2026-08-31T22:01:47+08:00"}]},
            {"notifications": [{"notification_id": "stable", "source_event_id": fact, "lifecycle_status": "CREATED", "created_at": "2026-08-31T21:59:00+08:00"}]},
        )
        self.assertEqual(len(merged["notifications"]), 1)
        self.assertEqual(merged["notifications"][0]["lifecycle_status"], "SENT")

    def test_missing_market_observation_time_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            event = {"key": "missing-time", "event_type": "MARKET_VALUE_ALERT", "title": "市场异动", "content": "无时点", "confirmation_context": {"market_date": "2026-09-01", "event_category": "EXTREME"}}
            with patch.object(notifications, "NOTIFICATION_STATE", Path(td) / "notification_center.json"), patch.object(notifications, "STATE", Path(td)):
                result = notifications.persist_and_send(event, policy="test")
            self.assertEqual(result["status"], "REJECTED_UNAUDITABLE_MARKET_EVENT")

    def test_stale_market_fact_cannot_enter_live_notification_channel(self):
        with tempfile.TemporaryDirectory() as td:
            event = {
                "key": "hstech-stale-fact",
                "event_type": "MARKET_VALUE_ALERT",
                "title": "【市场异动】恒生科技指数（HSTECH）日内方向明显反转",
                "content": "旧事实",
                "source": "market_delta",
                "security_code": "HSTECH",
                "confirmation_context": {
                    "market_date": "2026-08-31",
                    "event_category": "REVERSAL",
                    "direction": "UP",
                    "event_magnitude_pct": 1.7,
                    "day_change_pct": 0.32,
                    "market_as_of_beijing": "2026-08-31T16:09:08+08:00",
                },
            }
            with patch.object(notifications, "NOTIFICATION_STATE", Path(td) / "notification_center.json"), patch.object(notifications, "STATE", Path(td)), patch.object(notifications, "now", return_value=datetime.fromisoformat("2026-08-31T22:01:00+08:00")):
                result = notifications.persist_and_send(event, policy="test")
            self.assertEqual(result["status"], "REJECTED_STALE_MARKET_EVENT")

    def test_post_cutoff_market_fact_is_explicit_increment(self):
        event = {
            "event_type": "MARKET_VALUE_ALERT",
            "title": "【市场异动】半导体ETF代理（SOXX）出现极端波动",
            "content": "新增事实",
            "security_code": "SOXX",
            "confirmation_context": {
                "market_date": "2026-09-01",
                "event_category": "EXTREME",
                "direction": "DOWN",
                "event_magnitude_pct": 2.75,
                "market_as_of_beijing": "2026-09-01T05:17:23+08:00",
            },
        }
        summary = {
            "event_type": "US_SESSION_SUMMARY",
            "created_at": "2026-09-01T05:18:45+08:00",
            "sent_at": "2026-09-01T05:18:46+08:00",
            "confirmation_context": {"market_as_of_beijing": "2026-09-01T05:15:00+08:00"},
        }
        out = notifications._annotate_summary_increment(event, [summary])
        self.assertEqual(out["confirmation_context"]["summary_relation"], "NEW_FACT_AFTER_SUMMARY_CUTOFF")
        self.assertIn("新增事实", out["content"])

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


