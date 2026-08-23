from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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

    def test_conflict_stops_overwrite(self) -> None:
        path = self.root / "data" / "state" / "conflict.json"
        atomic_json_write(path, {"version": 1})
        expected = "0" * 64
        with self.assertRaises(StateConflictError):
            atomic_json_write(path, {"version": 2}, expected_sha256=expected)


if __name__ == "__main__":
    unittest.main()
