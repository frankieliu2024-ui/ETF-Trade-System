from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_post_market_review import build as build_post_market_review
from scripts.build_query_context import build as build_query_context
from scripts.state_manager import atomic_json_write, update_current


class ModeOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "state").mkdir(parents=True)
        (self.root / "system").mkdir()
        (self.root / "system" / "ETF当前状态_DASHBOARD.md").write_text("read-only dashboard", encoding="utf-8")
        atomic_json_write(self.root / "data" / "state" / "account_fact.json", {"updated_at": "", "source": "BROKER_SCREENSHOT", "status": "MISSING", "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_query_context_is_on_demand_and_account_safe(self) -> None:
        context = build_query_context(self.root)
        self.assertTrue(context["read_only"])
        self.assertEqual(context["account_fact_status"], "MISSING")
        self.assertTrue(context["needs_account_screenshot"])

    def test_post_market_missing_account_waits_for_user(self) -> None:
        update_current(root=self.root, market_date="2026-08-21", node="1500", captured_at="2026-08-21T15:00:00+08:00", node_status="READY", data_freshness={"status": "FRESH", "provider": "hithink-finance"})
        result = build_post_market_review(self.root)
        self.assertTrue(result["event"]["market_close"])
        self.assertEqual(result["event"]["status"], "WAITING_USER")
        self.assertFalse(result["review"]["formal_review_allowed"])

    def test_post_market_valid_placeholder_allows_review_preparation(self) -> None:
        atomic_json_write(self.root / "data" / "state" / "account_fact.json", {"updated_at": "2026-08-21T15:10:00+08:00", "source": "BROKER_SCREENSHOT", "status": "VALID", "simulation_only": True, "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []})
        update_current(root=self.root, market_date="2026-08-21", node="1500", captured_at="2026-08-21T15:00:00+08:00", node_status="READY", data_freshness={"status": "FRESH", "provider": "hithink-finance"})
        result = build_post_market_review(self.root)
        self.assertEqual(result["event"]["status"], "READY_FOR_REVIEW")
        self.assertTrue(result["review"]["formal_review_allowed"])
        self.assertFalse(result["review"]["formal_review_generated"])

    def test_non_close_does_not_wait_for_post_market_review(self) -> None:
        update_current(root=self.root, market_date="2026-08-21", node="1130", captured_at="2026-08-21T11:30:00+08:00", node_status="READY", data_freshness={"status": "FRESH"})
        result = build_post_market_review(self.root)
        self.assertFalse(result["event"]["market_close"])
        self.assertEqual(result["event"]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
