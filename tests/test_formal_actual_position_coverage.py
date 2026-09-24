import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import cloud_runner_snapshot
from scripts import state_manager
from scripts import build_query_context


class FormalActualPositionCoverageTests(unittest.TestCase):
    def test_market_acquisition_overlays_same_day_actual_held_etf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config/market").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "561980", "thscode": "561980.SH"}]}),
                encoding="utf-8",
            )
            (root / "data/state/account_fact.json").write_text(
                json.dumps({"positions": [
                    {"asset_type": "ETF", "code": "159981", "name": "能源化工ETF", "quantity": 2800},
                    {"asset_type": "ETF", "code": "159999", "name": "已清仓ETF", "quantity": 0},
                ]}),
                encoding="utf-8",
            )
            with patch.object(cloud_runner_snapshot, "ROOT", root):
                rows = cloud_runner_snapshot.load_etf_universe()
            self.assertIn(("159981", "159981.SZ"), rows)
            self.assertNotIn(("159999", "159999.SZ"), rows)

    def test_analysis_coverage_marks_current_held_etf_missing_even_if_not_persisted_universe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config/market").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "561980"}]}), encoding="utf-8"
            )
            (root / "data/state/stock_market_context.json").write_text(
                json.dumps({"objects": {}}), encoding="utf-8"
            )
            account = {"positions": [{"asset_type": "ETF", "code": "159981", "quantity": 2800}]}
            snapshot = {"rows": [
                {"asset_class": "ETF", "symbol": "561980", "quality_status": "PASS"},
                {"asset_class": "A_SHARE_INDEX", "symbol": "000001", "quality_status": "PASS"},
                {"asset_class": "A_SHARE_INDEX", "symbol": "000688", "quality_status": "PASS"},
                {"asset_class": "A_SHARE_INDEX", "symbol": "399006", "quality_status": "PASS"},
            ]}
            quality = {"failed_count": 0, "degraded_count": 0, "stale_count": 0,
                       "failed_objects": [], "degraded_objects": [], "stale_objects": []}
            coverage = state_manager.build_analysis_coverage(root, snapshot, account, quality)
            self.assertEqual(coverage["held_etfs_total"], 1)
            self.assertEqual(coverage["held_etfs_available"], 0)
            self.assertEqual(coverage["missing_held_etfs"], ["159981"])
            self.assertEqual(coverage["coverage_status"], "INCOMPLETE")

    def test_formal_action_readiness_fails_closed_when_actual_holding_market_coverage_incomplete(self):
        request = {
            "request_id": "formal-holding-gap",
            "requested_at_beijing": "2026-09-24T11:34:41+08:00",
            "requested_by": "CHATGPT_USER_INTERACTION",
            "query_intent": "FORMAL_DECISION",
            "_request_file": "requests/live_snapshot/formal-holding-gap.json",
        }
        account = {"status": "VALID", "positions": [
            {"asset_type": "ETF", "code": "159981", "name": "能源化工ETF", "quantity": 2800}
        ]}
        decision = {"analysis_coverage": {
            "held_etfs_total": 1, "held_etfs_available": 0,
            "account_stocks_total": 0, "account_stocks_available": 0,
            "missing_held_etfs": ["159981"], "missing_account_stocks": [],
        }}
        market_quote = {"decision_freshness": {"post_request": True, "resolved_post_request": True}, "quotes": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": []}), encoding="utf-8"
            )
            pack = build_query_context.build_decision_fact_pack(
                root, request, {"market_date": "2026-09-24"}, account, decision, market_quote,
                formal_discovery={"status": "PASS", "candidates": []},
            )
        self.assertFalse(pack["formal_action_readiness"]["ready"])
        self.assertIn("ACTUAL_POSITION_MARKET_COVERAGE_INCOMPLETE", pack["formal_action_readiness"]["blockers"])
        self.assertEqual(pack["formal_action_readiness"]["missing_held_etfs"], ["159981"])

    def test_formal_action_readiness_allows_complete_actual_position_coverage(self):
        request = {
            "request_id": "formal-holding-complete",
            "requested_at_beijing": "2026-09-24T13:01:00+08:00",
            "requested_by": "CHATGPT_USER_INTERACTION",
            "query_intent": "FORMAL_DECISION",
            "_request_file": "requests/live_snapshot/formal-holding-complete.json",
        }
        account = {"status": "VALID", "positions": [
            {"asset_type": "ETF", "code": "159981", "name": "能源化工ETF", "quantity": 2800}
        ]}
        decision = {"analysis_coverage": {
            "held_etfs_total": 1, "held_etfs_available": 1,
            "account_stocks_total": 0, "account_stocks_available": 0,
            "missing_held_etfs": [], "missing_account_stocks": [],
        }}
        market_quote = {"decision_freshness": {"post_request": True, "resolved_post_request": True}, "quotes": [
            {"symbol": "159981", "price": 1.73, "quality_status": "PASS", "data_time_beijing": "2026-09-24T13:01:01+08:00"}
        ]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": []}), encoding="utf-8"
            )
            pack = build_query_context.build_decision_fact_pack(
                root, request, {"market_date": "2026-09-24"}, account, decision, market_quote,
                formal_discovery={"status": "PASS", "candidates": []},
            )
        self.assertTrue(pack["formal_action_readiness"]["ready"])
        self.assertTrue(pack["formal_action_readiness"]["actual_position_market_coverage_complete"])


if __name__ == "__main__":
    unittest.main()
