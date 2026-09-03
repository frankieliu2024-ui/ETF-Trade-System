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
    def test_canonical_known_ipo_attribution_is_consumed_without_reinference(self):
        from scripts import notification_center as center
        account = {
            "positions": [{"code": "301689", "quantity": 500, "cost": 16.0}],
            "settlement_obligations": [{
                "obligation_type": "IPO_ALLOTMENT_PAYMENT", "security_code": "301689",
                "quantity": 500, "subscription_price": 16, "required_cash": 8000,
                "status": "SETTLED",
            }],
            "account_change_events_after_confirmed_at": [{
                "event_id": "ipo-registration", "code": "301689", "quantity_before": 0,
                "quantity_after": 500, "quantity_delta": 500,
                "reconciliation_status": "RECONCILED_BY_KNOWN_IPO_REGISTRATION",
                "event_time": "2026-09-02T10:12:00+08:00",
            }],
        }
        self.assertEqual(center._meaningful_unreconciled_account_changes(account), [])

    def test_unknown_position_still_uses_ssot_account_confirmation_title(self):
        from scripts import notification_center as center
        account = {"positions": [], "settlement_obligations": [], "account_change_events_after_confirmed_at": [{
            "event_id": "unknown-position", "event_time": "2026-09-02T10:12:00+08:00",
            "object": "未知ETF", "code": "999999", "quantity_before": 0, "quantity_after": 100,
            "quantity_delta": 100, "reconciliation_status": "UNRECONCILED_ACCOUNT_CHANGE",
        }]}
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "account_fact.json").write_text(json.dumps(account, ensure_ascii=False), encoding="utf-8")
            with patch.object(center, "STATE", state), patch.object(center, "now", return_value=datetime.fromisoformat("2026-09-02T10:13:00+08:00")):
                event = center.account_confirmation_event()
        self.assertIsNotNone(event)
        self.assertEqual(event["title"], "【账户确认】发现未解释的持仓/资金变化")

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

    def test_notification_family_absorbs_same_category_over_multiple_runs(self):
        from scripts import market_notification_common as common
        items = [{"event_type": "MARKET_VALUE_ALERT", "security_code": "APAC_DIVERGENCE", "created_at": "2026-09-01T09:48:00+08:00", "sent_at": "2026-09-01T09:48:00+08:00", "confirmation_context": {"market": "ASIA", "market_date": "2026-09-01", "session": "DAY", "event_category": "DIVERGENCE", "direction": "DIVERGED", "event_magnitude_pct": 1.89, "source_fact_id": "fact-0948", "event_family_id": "ASIA:2026-09-01:DAY:APAC_DIVERGENCE:DIVERGENCE:DIVERGED", "family_peak_magnitude_pct": 1.89}}]
        event = {"event_type": "MARKET_VALUE_ALERT", "security_code": "APAC_DIVERGENCE", "confirmation_context": {"market": "ASIA", "market_date": "2026-09-01", "session": "DAY", "event_category": "DIVERGENCE", "direction": "DIVERGED", "event_magnitude_pct": 2.41, "source_fact_id": "fact-1023"}, "created_at": "2026-09-01T10:23:00+08:00"}
        self.assertIsNotNone(common._find_aggregate_target(items, event))

    def test_divergence_material_upgrade_uses_highest_sent_baseline(self):
        from scripts import market_notification_common as common
        prior = {"event_type": "MARKET_VALUE_ALERT", "security_code": "APAC_DIVERGENCE", "confirmation_context": {"market": "ASIA", "market_date": "2026-09-01", "session": "DAY", "event_category": "DIVERGENCE", "direction": "DIVERGED", "event_magnitude_pct": 2.41, "family_peak_magnitude_pct": 2.41}}
        event = {"event_type": "MARKET_VALUE_ALERT", "security_code": "APAC_DIVERGENCE", "confirmation_context": {"market": "ASIA", "market_date": "2026-09-01", "session": "DAY", "event_category": "DIVERGENCE", "direction": "DIVERGED", "event_magnitude_pct": 2.47}}
        self.assertFalse(common._is_material_upgrade(event, prior))
        event["confirmation_context"]["event_magnitude_pct"] = 3.30
        self.assertTrue(common._is_material_upgrade(event, prior))

    def test_reversal_same_family_small_recovery_is_absorbed(self):
        from scripts import market_notification_common as common
        items = [{"event_type": "MARKET_VALUE_ALERT", "security_code": "KOSPI", "sent_at": "2026-09-01T09:41:00+08:00", "confirmation_context": {"market": "ASIA", "market_date": "2026-09-01", "session": "DAY", "event_category": "REVERSAL", "direction": "UP", "event_magnitude_pct": 1.28, "family_peak_magnitude_pct": 1.28}}]
        event = {"event_type": "MARKET_VALUE_ALERT", "security_code": "KOSPI", "confirmation_context": {"market": "ASIA", "market_date": "2026-09-01", "session": "DAY", "event_category": "REVERSAL", "direction": "UP", "event_magnitude_pct": 0.18}}
        self.assertIsNotNone(common._find_aggregate_target(items, event))

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



class NotificationDecisionIdentityTests(unittest.TestCase):
    NOW = datetime.fromisoformat("2026-09-03T14:00:00+08:00")

    def _run(self, items, matches):
        import scripts.notification_center as center
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "execution_reconciliation.json").write_text(
                json.dumps({"status": "RECONCILED", "matches": matches}),
                encoding="utf-8",
            )
            with patch.object(center, "STATE", state), patch.object(
                center, "now", return_value=self.NOW
            ):
                return center.revalidate_pending_notifications(items)

    def test_same_code_newer_decision_does_not_archive_old_prompt(self):
        item = {"notification_id": "old", "event_type": "PENDING_EXECUTION_CONFIRMATION",
                "related_decision_id": "decision-old", "security_code": "518880",
                "lifecycle_status": "WAITING_CONFIRMATION"}
        matches = [{"intent": {"decision_id": "decision-new", "code": "518880"},
                    "requires_user_confirmation": False}]
        self.assertEqual(self._run([item], matches)[0]["lifecycle_status"],
                         "WAITING_CONFIRMATION")

    def test_exact_reconciled_decision_archives(self):
        item = {"notification_id": "exact", "event_type": "PENDING_EXECUTION_CONFIRMATION",
                "related_decision_id": "decision-1", "security_code": "159326",
                "lifecycle_status": "WAITING_CONFIRMATION"}
        matches = [{"intent": {"decision_id": "decision-1", "code": "159326"},
                    "requires_user_confirmation": False}]
        self.assertEqual(self._run([item], matches)[0]["lifecycle_status"], "ARCHIVED")

    def test_exact_confirmation_required_is_kept(self):
        item = {"notification_id": "pending", "event_type": "PENDING_EXECUTION_CONFIRMATION",
                "related_decision_id": "decision-1", "security_code": "159326",
                "lifecycle_status": "WAITING_CONFIRMATION"}
        matches = [{"intent": {"decision_id": "decision-1", "code": "159326"},
                    "requires_user_confirmation": True}]
        self.assertEqual(self._run([item], matches)[0]["lifecycle_status"],
                         "WAITING_CONFIRMATION")

    def test_unique_legacy_identity_can_archive(self):
        item = {"notification_id": "legacy", "event_type": "PENDING_EXECUTION_CONFIRMATION",
                "security_code": "159326",
                "confirmation_context": {"side": "BUY", "lifecycle": "Trial",
                    "suggested_execution_date": "2026-09-02"},
                "lifecycle_status": "WAITING_CONFIRMATION"}
        matches = [{"intent": {"code": "159326", "side": "BUY", "lifecycle": "Trial"},
                    "execution_date": "2026-09-02", "requires_user_confirmation": False}]
        self.assertEqual(self._run([item], matches)[0]["lifecycle_status"], "ARCHIVED")

    def test_ambiguous_legacy_identity_is_fail_safe(self):
        item = {"notification_id": "ambiguous", "event_type": "PENDING_EXECUTION_CONFIRMATION",
                "security_code": "159326",
                "confirmation_context": {"side": "BUY", "lifecycle": "Trial"},
                "lifecycle_status": "WAITING_CONFIRMATION"}
        matches = [{"intent": {"code": "159326", "side": "BUY", "lifecycle": "Trial"},
                    "requires_user_confirmation": False},
                   {"intent": {"code": "159326", "side": "BUY", "lifecycle": "Trial"},
                    "requires_user_confirmation": False}]
        self.assertEqual(self._run([item], matches)[0]["lifecycle_status"],
                         "WAITING_CONFIRMATION")

    def test_account_confirmation_is_preserved(self):
        item = {"notification_id": "account", "event_type": "ACCOUNT_FACT_CONFIRMATION",
                "lifecycle_status": "WAITING_CONFIRMATION"}
        self.assertEqual(self._run([item], [])[0]["lifecycle_status"],
                         "WAITING_CONFIRMATION")


if __name__ == "__main__":
    unittest.main()

