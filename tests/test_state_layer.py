from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts import process_state_sync_request as state_sync
from scripts.process_state_sync_request import (
    CANONICAL_INGRESS_FAILED_EXPLICITLY,
    CANONICAL_INGRESS_NOT_APPLICABLE,
    CANONICAL_INGRESS_SUBMITTED,
    account_fact_is_older,
    canonical_ingress_contract_for_request,
    is_broker_screenshot_request,
    merge_account_fact,
    sync_current_account_mirror,
)
from scripts import build_e2e_status as e2e

from scripts.state_manager import (
    StateConflictError,
    append_event,
    atomic_json_write,
    build_dashboard_candidate,
    build_decision_context,
    read_current,
    update_current,
)


class StateLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "state").mkdir(parents=True)
        (self.root).mkdir(parents=True, exist_ok=True)
        (self.root / "ETF当前状态_DASHBOARD.md").write_text("manual dashboard", encoding="utf-8")
        (self.root / "data" / "state" / "account_fact.json").write_text(json.dumps({
            "updated_at": "", "source": "BROKER_SCREENSHOT", "status": "MISSING",
            "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": [],
        }), encoding="utf-8")
        update_current(root=self.root, market_date="", node="", captured_at="", node_status="NON_TRADING_DAY")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_current_latest_node_and_supersession(self) -> None:
        update_current(root=self.root, market_date="2026-08-24", node="10:30", captured_at="2026-08-24T10:30:00+08:00", latest_snapshot="data/market/snapshots/10.json")
        update_current(root=self.root, market_date="2026-08-24", node="11:30", captured_at="2026-08-24T11:30:00+08:00", latest_snapshot="data/market/snapshots/11.json")
        current = read_current(self.root)
        self.assertEqual(current["latest_valid_node"], "11:30")
        self.assertEqual(current["latest_snapshot"], "data/market/snapshots/11.json")
        self.assertEqual(current["superseded_nodes"][0]["node"], "10:30")

    def test_old_notification_resolves_to_current(self) -> None:
        update_current(root=self.root, market_date="2026-08-24", node="10:30", captured_at="2026-08-24T10:30:00+08:00")
        update_current(root=self.root, market_date="2026-08-24", node="11:30", captured_at="2026-08-24T11:30:00+08:00")
        self.assertEqual(read_current(self.root)["latest_valid_node"], "11:30")

    def test_missing_account_blocks_formal_amount(self) -> None:
        update_current(root=self.root, market_date="2026-08-24", node="10:30", captured_at="2026-08-24T10:30:00+08:00")
        candidate = build_dashboard_candidate(self.root)
        context = build_decision_context(self.root)
        self.assertTrue(candidate["needs_account_update"])
        self.assertTrue(context["needs_account_screenshot"])
        self.assertNotIn("trade_amount", candidate)

    def test_data_failure_is_degraded(self) -> None:
        update_current(root=self.root, market_date="2026-08-24", node="11:30", captured_at="2026-08-24T11:30:00+08:00", node_status="DEGRADED", data_freshness={"status": "DATA_ERROR"})
        self.assertEqual(read_current(self.root)["node_status"], "DEGRADED")

    def test_non_trading_day_does_not_create_valid_node(self) -> None:
        update_current(root=self.root, market_date="2026-08-24", node="10:30", captured_at="2026-08-24T10:30:00+08:00")
        update_current(root=self.root, market_date="2026-08-23", node="", captured_at="2026-08-23T09:25:00+08:00", node_status="NON_TRADING_DAY")
        current = read_current(self.root)
        self.assertEqual(current["latest_valid_node"], "10:30")
        self.assertEqual(current["node_status"], "NON_TRADING_DAY")

    def test_event_is_idempotent(self) -> None:
        payload = {"trade_id": "fixture-001", "fact_only": True}
        first, created = append_event(root=self.root, event_type="TRADE_EXECUTED", source="fixture", payload=payload, git_commit="abc")
        second, duplicate = append_event(root=self.root, event_type="TRADE_EXECUTED", source="fixture", payload=payload, git_commit="abc")
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(len((self.root / "events" / "events.jsonl").read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(read_current(self.root)["last_trade_event_id"], first["event_id"])

    def test_decision_context_has_interaction_safe_dashboard_summary(self) -> None:
        context = build_decision_context(self.root)
        self.assertIn("market_date", context)
        self.assertFalse(context["dashboard_summary"]["automatic_overwrite"])
        self.assertTrue(context["needs_account_screenshot"])



    def test_account_sync_updates_current_account_mirror(self) -> None:
        account = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00", "source": "BROKER_SCREENSHOT_20260831"}
        sync_current_account_mirror(self.root, account)
        current = read_current(self.root)
        self.assertEqual(current["account_fact"]["updated_at"], account["updated_at"])
        self.assertEqual(current["account_fact"]["source"], account["source"])
        self.assertFalse(current["needs_account_update"])

    def test_broker_snapshot_merge_carries_forward_canonical_history(self) -> None:
        prior = {
            "status": "VALID",
            "updated_at": "2026-08-28T14:18:00+08:00",
            "source": "BROKER_SCREENSHOT_20260828",
            "total_asset": 100.0,
            "positions": [{"code": "515880", "quantity": 10}],
            "trades": [{"event_id": "trade-1"}],
            "formal_action": {"decision_id": "decision-1", "execution_status": "EXECUTED"},
            "fee_facts": [{"fee": 5.0}],
            "account_change_events_after_confirmed_at": [{"idempotency_key": "event-1"}],
        }
        incoming = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00", "source": "BROKER_SCREENSHOT_20260831", "total_asset": 110.0, "positions": [{"code": "515880", "quantity": 11}]}
        merged = merge_account_fact(prior, incoming)
        self.assertEqual(merged["total_asset"], 110.0)
        self.assertEqual(merged["trades"], prior["trades"])
        self.assertEqual(merged["formal_action"], prior["formal_action"])
        self.assertEqual(merged["fee_facts"], prior["fee_facts"])
        self.assertEqual(merged["account_change_events_after_confirmed_at"], prior["account_change_events_after_confirmed_at"])

    def test_older_broker_snapshot_cannot_replace_newer_account(self) -> None:
        prior = {"updated_at": "2026-08-31T15:12:00+08:00"}
        incoming = {"updated_at": "2026-08-31T14:01:00+08:00"}
        self.assertTrue(account_fact_is_older(prior, incoming))

    def test_broker_request_without_account_fact_is_explicit(self) -> None:
        self.assertTrue(is_broker_screenshot_request({"source": "CHATGPT_USER_BROKER_SCREENSHOT"}))
        self.assertTrue(is_broker_screenshot_request({"interaction_scenario": "BROKER_SCREENSHOT_SYNC"}))
        self.assertFalse(is_broker_screenshot_request({"interaction_scenario": "FORMAL_INTRADAY_ANALYSIS"}))

    def test_real_20260904_broker_refresh_shape_is_explicitly_classified(self) -> None:
        request = json.loads(
            (Path(__file__).resolve().parents[1] / "requests/live_snapshot/20260904_1327_user_screenshot.json").read_text(encoding="utf-8")
        )
        self.assertTrue(is_broker_screenshot_request(request))

    def test_stable_fact_type_classifies_broker_without_source_alias(self) -> None:
        self.assertTrue(is_broker_screenshot_request({"fact_type": "BROKER_ACCOUNT_SNAPSHOT"}))
        self.assertTrue(is_broker_screenshot_request({"formal_fact_type": "ACCOUNT_FACT_CONFIRMATION"}))
        self.assertFalse(is_broker_screenshot_request({"fact_type": "MARKET_SNAPSHOT"}))

    def test_e2e_blocks_new_unprocessed_broker_request(self) -> None:
        request_dir = self.root / "requests" / "live_snapshot"
        request_dir.mkdir(parents=True)
        (request_dir / "20260831_1513_broker.json").write_text(json.dumps({
            "request_id": "broker-1513",
            "request_time_beijing": "2026-08-31T15:13:00+08:00",
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "interaction_scenario": "BROKER_SCREENSHOT_SYNC",
        }), encoding="utf-8")
        account = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00", "source": "BROKER_SCREENSHOT_20260831"}
        current = {"needs_account_update": False}
        with patch.object(e2e, "ROOT", self.root):
            result = e2e.account_component(account, current)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("ACCOUNT_SYNC_NOT_PERFORMED", result["reason"])

    def test_conflict_stops_overwrite(self) -> None:
        path = self.root / "data" / "state" / "conflict.json"
        atomic_json_write(path, {"version": 1})
        expected = "0" * 64
        with self.assertRaises(StateConflictError):
            atomic_json_write(path, {"version": 2}, expected_sha256=expected)


    def _write_broker_request(self, request: dict) -> None:
        request_dir = self.root / "requests" / "live_snapshot"
        request_dir.mkdir(parents=True, exist_ok=True)
        (request_dir / "20260905_120224_broker.json").write_text(
            json.dumps(request), encoding="utf-8"
        )

    def test_processed_late_broker_replay_is_not_account_block(self) -> None:
        account = {
            "status": "VALID",
            "updated_at": "2026-09-04T15:03:00+08:00",
            "source": "CANONICAL_ACCOUNT_FACT",
            "cash": 100.0,
            "positions": [{"code": "561980", "quantity": 100, "current_price": 0.644, "pnl": -12.5}],
        }
        supplied = {
            **account,
            "source": "BROKER_SCREENSHOT_REPLAY",
        }
        self._write_broker_request({
            "request_id": "broker-replay-processed",
            "requested_at_beijing": "2026-09-05T12:02:24+08:00",
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "interaction_scenario": "BROKER_SCREENSHOT_SYNC",
            "account_fact": supplied,
        })
        with patch.object(e2e, "ROOT", self.root):
            first = e2e.account_component(account, {"needs_account_update": False})
            second = e2e.account_component(account, {"needs_account_update": False})
        self.assertEqual(first["status"], "READY")
        self.assertEqual(second, first)
        self.assertNotIn("ACCOUNT_SYNC_NOT_PERFORMED", first["reason"])

    def test_newer_changed_broker_fact_still_blocks(self) -> None:
        account = {
            "status": "VALID",
            "updated_at": "2026-09-04T15:03:00+08:00",
            "source": "CANONICAL_ACCOUNT_FACT",
            "positions": [{"code": "561980", "quantity": 100}],
        }
        self._write_broker_request({
            "request_id": "broker-replay-new-fact",
            "requested_at_beijing": "2026-09-05T12:02:24+08:00",
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "interaction_scenario": "BROKER_SCREENSHOT_SYNC",
            "account_fact": {
                **account,
                "updated_at": "2026-09-05T12:02:24+08:00",
                "positions": [{"code": "561980", "quantity": 200}],
            },
        })
        with patch.object(e2e, "ROOT", self.root):
            result = e2e.account_component(account, {"needs_account_update": False})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("ACCOUNT_SYNC_NOT_PERFORMED", result["reason"])

    def test_request_without_account_fact_still_blocks(self) -> None:
        request_dir = self.root / "requests" / "live_snapshot"
        request_dir.mkdir(parents=True, exist_ok=True)
        (request_dir / "20260905_120224_missing.json").write_text(json.dumps({
            "request_id": "broker-missing-fact",
            "requested_at_beijing": "2026-09-05T12:02:24+08:00",
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "interaction_scenario": "BROKER_SCREENSHOT_SYNC",
        }), encoding="utf-8")
        account = {"status": "VALID", "updated_at": "2026-09-04T15:03:00+08:00", "positions": []}
        with patch.object(e2e, "ROOT", self.root):
            result = e2e.account_component(account, {"needs_account_update": False})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("ACCOUNT_SYNC_NOT_PERFORMED", result["reason"])



def test_request_bound_formal_reply_gate_blocks_on_unconsumed_broker_fact(tmp_path):
    from scripts import build_e2e_status as e2e

    root = tmp_path
    state = root / "data" / "state"
    requests = root / "requests" / "live_snapshot"
    state.mkdir(parents=True)
    requests.mkdir(parents=True)

    account = {
        "status": "VALID",
        "updated_at": "2026-09-29T09:40:00+08:00",
        "cash": 21962.84,
        "positions": [{"code": "159941", "quantity": 12200}],
    }
    current = {
        "needs_account_update": False,
        "account_fact": {"status": "VALID", "updated_at": account["updated_at"]},
    }
    broker = {
        "request_id": "broker-159992",
        "requested_at_beijing": "2026-09-29T09:51:00+08:00",
        "source": "CHATGPT_USER_BROKER_SCREENSHOT",
        "account_change_event_type": "USER_REPORTED_TRADE",
        "account_fact": {
            "status": "VALID",
            "updated_at": "2026-09-29T09:50:30+08:00",
            "cash": 17004.64,
            "positions": [
                {"code": "159941", "quantity": 12200},
                {"code": "159992", "quantity": 5800},
            ],
        },
        "trade_event": {
            "event_id": "trade-159992",
            "code": "159992",
            "side": "BUY",
            "quantity": 5800,
            "price": 0.854,
            "amount": 4953.20,
            "executed_at": "2026-09-29T09:50:25+08:00",
        },
    }
    query = {
        "decision_fact_pack": {
            "trigger": {
                "request_id": "formal-0955",
                "requested_at_beijing": "2026-09-29T09:55:00+08:00",
            },
            "formal_reply_freeze": {
                "status": "READY",
                "reply_freezable": True,
                "blockers": [],
            },
        },
        "fast_path_latency": {"latency_status": "OBSERVED"},
    }
    decision = {
        "observability": {},
        "formal_intraday_context_completeness": {"status": "READY"},
    }

    (state / "account_fact.json").write_text(json.dumps(account), encoding="utf-8")
    (state / "CURRENT.json").write_text(json.dumps(current), encoding="utf-8")
    (requests / "broker-159992.json").write_text(json.dumps(broker), encoding="utf-8")

    old_root, old_state, old_files = e2e.ROOT, e2e.STATE, e2e.FILES
    try:
        e2e.ROOT = root
        e2e.STATE = state
        e2e.FILES = {
            **old_files,
            "account": state / "account_fact.json",
            "current": state / "CURRENT.json",
        }
        component = e2e.context_component(query, decision)
        gate = component["formal_reply_gate"]
        assert gate["reply_freezable"] is False
        assert gate["status"] == "BLOCKED"
        assert any("ACCOUNT_SYNC_NOT_PERFORMED" in item for item in gate["blockers"])
        assert gate["account_dependency"]["pending_broker_request"]["request_id"] == "broker-159992"
    finally:
        e2e.ROOT, e2e.STATE, e2e.FILES = old_root, old_state, old_files

if __name__ == "__main__":
    unittest.main()


def test_actor_adopted_broker_fact_has_terminal_ingress_contract():
    submitted = canonical_ingress_contract_for_request({
        "_ingress_path": "requests/live_snapshot/20260914_broker_account_snapshot.json",
        "request_id": "20260914_broker_account_snapshot",
        "formal_fact_type": "BROKER_ACCOUNT_SNAPSHOT",
        "account_fact": {"cash": 1},
    })
    assert submitted["required"] is True
    assert submitted["terminal_state"] == CANONICAL_INGRESS_SUBMITTED

    request_id_only = canonical_ingress_contract_for_request({
        "request_id": "20260914_broker_account_snapshot",
        "formal_fact_type": "BROKER_ACCOUNT_SNAPSHOT",
        "account_fact": {"cash": 1},
    })
    assert request_id_only["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
    assert request_id_only["reason"] == "missing_durable_ingress_receipt"

    failed = canonical_ingress_contract_for_request({
        "_ingress_path": "requests/live_snapshot/20260914_broker_account_snapshot_missing_payload.json",
        "request_id": "20260914_broker_account_snapshot_missing_payload",
        "formal_fact_type": "BROKER_ACCOUNT_SNAPSHOT",
    })
    assert failed["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
    assert failed["reason"] == "missing_account_fact"


def test_adopted_formal_fact_without_durable_receipt_fails_explicitly():
    failed = canonical_ingress_contract_for_request({
        "formal_fact_type": "BROKER_ACCOUNT_SNAPSHOT",
        "account_fact": {"cash": 1},
    })

    assert failed["required"] is True
    assert failed["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
    assert failed["reason"] == "missing_durable_ingress_receipt"


def test_scheduled_review_report_delivery_cannot_substitute_for_canonical_ingress():
    report_only = canonical_ingress_contract_for_request({
        "_ingress_path": "requests/live_snapshot/20260914_report_delivery.json",
        "request_id": "20260914_report_delivery",
        "channel": "REPORT",
        "report_type": "ETF_TRADE_REVIEW",
    })
    assert report_only["terminal_state"] == CANONICAL_INGRESS_NOT_APPLICABLE

    missing_review_payload = canonical_ingress_contract_for_request({
        "_ingress_path": "requests/live_snapshot/20260914_scheduled_review.json",
        "request_id": "20260914_scheduled_review",
        "formal_fact_type": "FORMAL_POST_CLOSE_REVIEW",
        "channel": "REPORT",
        "report_type": "ETF_TRADE_REVIEW",
    })
    assert missing_review_payload["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
    assert missing_review_payload["reason"] == "report_delivery_is_not_canonical_review_ingress"


def test_scheduled_review_formal_payload_reaches_canonical_ingress():
    submitted = canonical_ingress_contract_for_request({
        "_ingress_path": "requests/live_snapshot/20260914_scheduled_review.json",
        "request_id": "20260914_scheduled_review",
        "formal_fact_type": "FORMAL_POST_CLOSE_REVIEW",
        "interaction_scenario": "POST_CLOSE_REVIEW",
        "formal_review": {"date": "2026-09-14", "summary": "review"},
    })

    assert submitted["required"] is True
    assert submitted["terminal_state"] == CANONICAL_INGRESS_SUBMITTED


def test_confirmed_trade_and_formal_decision_keep_ingress_success_chain():
    trade = canonical_ingress_contract_for_request({
        "_ingress_path": "requests/live_snapshot/20260914_trade.json",
        "request_id": "20260914_trade",
        "formal_fact_type": "CONFIRMED_TRADE",
        "trade_event": {"symbol": "QQQ", "side": "BUY"},
    })
    decision = canonical_ingress_contract_for_request({
        "_ingress_path": "requests/live_snapshot/20260914_decision.json",
        "request_id": "20260914_decision",
        "formal_fact_type": "FORMAL_DECISION",
        "formal_decision": {"decision": "HOLD"},
    })

    assert trade["terminal_state"] == CANONICAL_INGRESS_SUBMITTED
    assert decision["terminal_state"] == CANONICAL_INGRESS_SUBMITTED


def test_real_20260904_broker_screenshot_replay_fails_explicitly_not_silently():
    request = json.loads(Path("requests/live_snapshot/20260904_1327_user_screenshot.json").read_text())
    request["_ingress_path"] = "requests/live_snapshot/20260904_1327_user_screenshot.json"

    contract = canonical_ingress_contract_for_request(request)

    assert contract["required"] is True
    assert contract["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
    assert contract["reason"] == "missing_account_fact"



def test_plural_broker_trade_events_are_consumed_without_event_id_collision(tmp_path, monkeypatch, capsys):
    request = {
        "request_id": "broker-batch",
        "source": "CHATGPT_USER_BROKER_SCREENSHOT",
        "formal_fact_type": "BROKER_TRADE_CONFIRMATION",
        "interaction_scenario": "INTRADAY",
        "requested_at_beijing": "2026-09-28T10:51:23+08:00",
        "trade_events": [
            {"code": "159981", "name": "能源化工ETF", "asset_type": "ETF", "side": "BUY", "quantity": 5700,
             "price": 1.75, "amount": 9975, "confirmed_at_beijing": "2026-09-28T10:51:05+08:00",
             "idempotency_key": "20260928_105105_159981_BUY_5700_1.750"},
            {"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF", "side": "SELL", "quantity": 15300,
             "price": 0.649, "amount": 9929.7, "confirmed_at_beijing": "2026-09-28T10:51:23+08:00",
             "idempotency_key": "20260928_105123_561980_SELL_15300_0.649"},
        ],
    }
    contract = canonical_ingress_contract_for_request({
        **request, "_ingress_path": "requests/live_snapshot/broker-batch.json",
    })
    assert contract["terminal_state"] == CANONICAL_INGRESS_SUBMITTED

    result = _run_processor_contract_case(tmp_path, monkeypatch, capsys, request)
    assert result["trade_event_recorded"] is True
    events = sorted((tmp_path / "events" / "trades").glob("*.json"))
    assert len(events) == 2
    saved = [json.loads(path.read_text(encoding="utf-8")) for path in events]
    assert {event["code"] for event in saved} == {"159981", "561980"}
    assert len({event["event_id"] for event in saved}) == 2
    assert {event["idempotency_key"] for event in saved} == {
        "20260928_105105_159981_BUY_5700_1.750",
        "20260928_105123_561980_SELL_15300_0.649",
    }




def test_market_snapshot_rebuilds_phase4_after_latest_main_reset():
    workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
    reset = workflow.index("git reset --hard origin/main", workflow.index("Commit downstream runtime/data artifacts"))
    rebuild = workflow.index("python scripts/build_state_context.py --decision-ready-only", reset)
    final_add = workflow.index("git add -A -- data/market/snapshots", rebuild)
    assert reset < rebuild < final_add
    publish_slice = workflow[reset:final_add]
    assert "data/state/decision_trigger.json" in publish_slice
    assert "data/state/capital_efficiency_ranking.json" in publish_slice
    assert "execution-time latest-main CURRENT" in publish_slice



def test_new_trade_updates_current_mirror_after_event_persistence(tmp_path, monkeypatch, capsys):
    request = {
        "request_id": "broker-current-mirror",
        "source": "CHATGPT_USER_BROKER_SCREENSHOT",
        "formal_fact_type": "BROKER_TRADE_CONFIRMATION",
        "interaction_scenario": "BROKER_SCREENSHOT_SYNC",
        "requested_at_beijing": "2026-09-30T09:45:00+08:00",
        "trade_event": {
            "event_id": "trade_20260930_094415_513520_buy_2100",
            "code": "513520",
            "name": "日经ETF",
            "asset_type": "ETF",
            "side": "BUY",
            "quantity": 2100,
            "price": 2.31,
            "amount": 4851,
            "confirmed_at_beijing": "2026-09-30T09:45:00+08:00",
            "idempotency_key": "20260930_094415_513520_BUY_2100_2.310",
        },
    }
    root = tmp_path
    (root / "requests" / "live_snapshot").mkdir(parents=True)
    (root / "data" / "state").mkdir(parents=True)
    (root / "events" / "trades").mkdir(parents=True)
    (root / "ETF当前状态_DASHBOARD.md").write_text("dashboard", encoding="utf-8")
    (root / "data" / "state" / "account_fact.json").write_text(
        json.dumps(_minimal_valid_account(), ensure_ascii=False), encoding="utf-8"
    )
    (root / "data" / "state" / "CURRENT.json").write_text(
        json.dumps({"last_trade_event_id": "older-trade"}), encoding="utf-8"
    )
    request_path = root / "requests" / "live_snapshot" / "case.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(state_sync, "ROOT", root)
    monkeypatch.setattr(state_sync, "ACCOUNT", root / "data" / "state" / "account_fact.json")
    monkeypatch.setattr(state_sync, "DASHBOARD", root / "ETF当前状态_DASHBOARD.md")
    monkeypatch.setattr(state_sync, "record_formal_decision", lambda payload: (False, ""))
    monkeypatch.setattr(state_sync, "record_post_close_review", lambda account, payload: (False, False))
    monkeypatch.setattr(state_sync, "record_unrecoverable_review_prerequisite", lambda account, payload, event: (False, False))
    monkeypatch.setattr(state_sync, "replace_block", lambda text, *args, **kwargs: text)
    monkeypatch.setattr(state_sync, "build_dashboard_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(state_sync, "write_formal_text_if_changed", lambda *args, **kwargs: False)
    monkeypatch.setattr(state_sync, "sync_formal_files", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(state_sync, "latest_formal_review_decision", lambda root: None)
    monkeypatch.setattr(state_sync, "sync_experience_transaction_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(state_sync, "upsert_formal_line", lambda *args, **kwargs: None)
    monkeypatch.setattr(state_sync, "write_trade_review_required", lambda *args, **kwargs: None)
    monkeypatch.setattr(state_sync, "sync_current_account_mirror", lambda *args, **kwargs: None)
    monkeypatch.setattr(state_sync, "account_membership_delta", lambda *args, **kwargs: {})
    monkeypatch.setattr(state_sync, "sync_account_membership_formal_files", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["process_state_sync_request.py", "requests/live_snapshot/case.json"])

    assert state_sync.main() == 0
    current = json.loads((root / "data" / "state" / "CURRENT.json").read_text(encoding="utf-8"))
    assert current["last_trade_event_id"] == "trade_20260930_094415_513520_buy_2100"



def test_account_change_events_reconcile_each_plural_confirmed_trade():
    prior = {
        "updated_at": "2026-09-24T09:53:00+08:00", "cash": 13169.54, "total_asset": 187900.0,
        "positions": [{"code": "159981", "quantity": 2800}, {"code": "561980", "quantity": 28900}],
    }
    current = {
        "updated_at": "2026-09-28T11:36:00+08:00", "cash": 13114.24, "total_asset": 187917.74,
        "positions": [{"code": "159981", "quantity": 8500}, {"code": "561980", "quantity": 13600}],
    }
    trades = [
        {"code": "159981", "side": "BUY", "quantity": 5700, "confirmed_at_beijing": "2026-09-28T10:51:05+08:00"},
        {"code": "561980", "side": "SELL", "quantity": 15300, "confirmed_at_beijing": "2026-09-28T10:51:23+08:00"},
    ]
    events = state_sync._account_change_events(
        prior, current, {"account_change_event_type": "USER_REPORTED_TRADE"}, trades
    )
    position_events = {event["code"]: event for event in events if event.get("code")}
    assert position_events["159981"]["reconciliation_status"] == "RECONCILED_BY_CONFIRMED_TRADE"
    assert position_events["159981"]["event_time"] == "2026-09-28T10:51:05+08:00"
    assert position_events["561980"]["reconciliation_status"] == "RECONCILED_BY_CONFIRMED_TRADE"
    assert position_events["561980"]["event_time"] == "2026-09-28T10:51:23+08:00"
    cash_event = next(event for event in events if event.get("object") == "cash")
    assert cash_event["reconciliation_status"] == "RECONCILED_BY_CONFIRMED_TRADE"

def _minimal_valid_account():
    return {
        "updated_at": "2026-09-14T15:00:00+08:00",
        "source": "BROKER_SCREENSHOT",
        "status": "VALID",
        "total_asset": 100000,
        "cash": 100000,
        "positions": [],
        "orders": [],
        "trades": [],
    }


def _run_processor_contract_case(tmp_path, monkeypatch, capsys, request):
    root = tmp_path
    request_dir = root / "requests" / "live_snapshot"
    request_dir.mkdir(parents=True)
    (root / "data" / "state").mkdir(parents=True)
    (root / "ETF当前状态_DASHBOARD.md").write_text("dashboard", encoding="utf-8")
    (root / "data" / "state" / "account_fact.json").write_text(
        json.dumps(_minimal_valid_account(), ensure_ascii=False),
        encoding="utf-8",
    )
    request_path = request_dir / "case.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    seen = []
    original_contract = state_sync.canonical_ingress_contract_for_request

    def capture_contract(payload):
        seen.append(dict(payload))
        return original_contract(payload)

    monkeypatch.setattr(state_sync, "ROOT", root)
    monkeypatch.setattr(state_sync, "ACCOUNT", root / "data" / "state" / "account_fact.json")
    monkeypatch.setattr(state_sync, "DASHBOARD", root / "ETF当前状态_DASHBOARD.md")
    monkeypatch.setattr(state_sync, "canonical_ingress_contract_for_request", capture_contract)
    monkeypatch.setattr(state_sync, "_latest_trade_event_id", lambda: "")
    monkeypatch.setattr(state_sync, "record_formal_decision", lambda payload: (bool(payload.get("formal_decision")), "decision-1" if payload.get("formal_decision") else ""))
    monkeypatch.setattr(state_sync, "record_post_close_review", lambda account, payload: (bool(payload.get("formal_review") or payload.get("review")), False))
    monkeypatch.setattr(state_sync, "record_unrecoverable_review_prerequisite", lambda account, payload, event: (False, False))
    monkeypatch.setattr(state_sync, "replace_block", lambda text, *args, **kwargs: text)
    monkeypatch.setattr(state_sync, "build_dashboard_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(state_sync, "write_formal_text_if_changed", lambda *args, **kwargs: False)
    monkeypatch.setattr(state_sync, "sync_formal_files", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(state_sync, "latest_formal_review_decision", lambda root: None)
    monkeypatch.setattr(state_sync, "_find_existing_trade", lambda *args, **kwargs: ({
        "event_id": "trade-1",
        "confirmed_at_beijing": "2026-09-14T10:00:00+08:00",
        "linked_decision_id": "decision-1",
    } if request.get("trade_event") else None))
    monkeypatch.setattr(state_sync, "_latest_trade_event_id", lambda: "")
    monkeypatch.setattr("sys.argv", ["process_state_sync_request.py", "requests/live_snapshot/case.json"])

    assert state_sync.main() == 0
    out = json.loads(capsys.readouterr().out)

    assert seen
    assert seen[0]["_ingress_path"] == "requests/live_snapshot/case.json"
    assert out["canonical_ingress_state"] == CANONICAL_INGRESS_SUBMITTED
    return out


def test_processor_injects_ingress_path_before_terminal_contract(tmp_path, monkeypatch, capsys):
    cases = [
        {
            "request_id": "broker",
            "formal_fact_type": "BROKER_ACCOUNT_SNAPSHOT",
            "account_fact": _minimal_valid_account(),
        },
        {
            "request_id": "review",
            "formal_fact_type": "FORMAL_POST_CLOSE_REVIEW",
            "formal_review": {"date": "2026-09-14", "summary": "review"},
        },
        {
            "request_id": "trade",
            "formal_fact_type": "CONFIRMED_TRADE",
            "trade_event": {
                "event_id": "trade-1",
                "symbol": "QQQ",
                "side": "BUY",
                "confirmed_at_beijing": "2026-09-14T10:00:00+08:00",
            },
        },
        {
            "request_id": "decision",
            "formal_fact_type": "FORMAL_DECISION",
            "formal_decision": {"decision_id": "decision-1", "action": "HOLD"},
        },
    ]

    for request in cases:
        _run_processor_contract_case(tmp_path / request["request_id"], monkeypatch, capsys, request)
