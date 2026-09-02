import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_execution_reconciliation as reconciliation
from scripts import check_system_consistency as consistency


class ExecutedTradeCaseLifecycleTests(unittest.TestCase):
    def test_same_day_intraday_pending_case_is_allowed(self):
        current = {"market_date": "2026-09-01", "latest_valid_node": "live", "data_freshness": {"market_phase": "CONTINUOUS_AFTERNOON"}}
        event = {"event_id": "trade-1", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}
        self.assertFalse(consistency._case_mapping_required(current, event))

    def test_post_close_requires_final_case_mapping(self):
        current = {"market_date": "2026-09-01", "latest_valid_node": "close", "data_freshness": {"market_phase": "POST_CLOSE_GRACE"}}
        event = {"event_id": "trade-1", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}
        self.assertTrue(consistency._case_mapping_required(current, event))

    def test_old_trade_requires_final_case_mapping_on_new_day(self):
        current = {"market_date": "2026-09-02", "latest_valid_node": "live", "data_freshness": {"market_phase": "CONTINUOUS_MORNING"}}
        event = {"event_id": "trade-1", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}
        self.assertTrue(consistency._case_mapping_required(current, event))

    def test_checker_accepts_pending_intraday_but_fails_post_close_until_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ETF市场行情档案_2026.md").write_text("trade-1｜archive\n", encoding="utf-8")
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text("trade-1｜- 待复盘CASE｜2026-09-01T14:40:01+08:00｜\nTRADE_EVENT:trade-1\n", encoding="utf-8")
            trade_dir = root / "events" / "trades"
            trade_dir.mkdir(parents=True)
            (trade_dir / "trade-1.json").write_text(json.dumps({"event_id": "trade-1", "execution_status": "EXECUTED", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}), encoding="utf-8")
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            current_path = state_dir / "CURRENT.json"
            current_path.write_text(json.dumps({"market_date": "2026-09-01", "latest_valid_node": "live", "data_freshness": {"market_phase": "CONTINUOUS_AFTERNOON"}}), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_trade_event_formal_sync(report)
                self.assertEqual(report["checks"][-1]["status"], "PASS")
                current_path.write_text(json.dumps({"market_date": "2026-09-01", "latest_valid_node": "close", "data_freshness": {"market_phase": "POST_CLOSE_GRACE"}}), encoding="utf-8")
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_trade_event_formal_sync(report)
                self.assertEqual(report["checks"][-1]["status"], "FAIL")
                self.assertIn("formal_trade_sync:trade-1:formal_case_mapping", report["errors"])
                experience.write_text("### CASE-20260901-01：review\ntrade-1｜- 已归入CASE-20260901-01｜2026-09-01T15:10:00+08:00｜\nTRADE_EVENT:trade-1\n", encoding="utf-8")
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_trade_event_formal_sync(report)
                self.assertEqual(report["checks"][-1]["status"], "PASS")

    def test_historical_index_pending_row_is_allowed_intraday_but_not_post_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ETF市场行情档案_2026.md").write_text("", encoding="utf-8")
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "共1笔证券交易：ETF 1笔、个股0笔\n"
                "|2026-09-01 14:40:01|测试ETF|561980|卖出|1|1|1|0|1|待复盘CASE|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            current_path = state_dir / "CURRENT.json"
            current_path.write_text(json.dumps({
                "market_date": "2026-09-01", "latest_valid_node": "live",
                "data_freshness": {"market_phase": "CONTINUOUS_AFTERNOON"},
            }), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_historical_trade_case_mapping(report)
                self.assertEqual(report["checks"][-1]["status"], "PASS")
                current_path.write_text(json.dumps({
                    "market_date": "2026-09-01", "latest_valid_node": "close",
                    "data_freshness": {"market_phase": "POST_CLOSE_GRACE"},
                }), encoding="utf-8")
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_historical_trade_case_mapping(report)
                self.assertEqual(report["checks"][-1]["status"], "FAIL")

    def test_exact_linked_trade_before_formal_decision_is_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision_dir = root / "events" / "decisions"
            trade_dir = root / "events" / "trades"
            decision_dir.mkdir(parents=True)
            trade_dir.mkdir(parents=True)
            decision_id = "20260902_141753_trade_159326"
            (decision_dir / f"{decision_id}.json").write_text(json.dumps({
                "decision_id": decision_id,
                "decision_time": "2026-09-02T14:21:31+08:00",
                "candidate": {"code": "159326"},
                "formal_decision": {"action": "BUY", "amount_action": "买入3000份，成交价1.651元，成交金额4953元"},
            }), encoding="utf-8")
            event_id = "20260902_141753_trade_159326"
            (trade_dir / f"{event_id}.json").write_text(json.dumps({
                "event_id": event_id,
                "code": "159326",
                "side": "BUY",
                "quantity": 3000,
                "price": 1.651,
                "amount_yuan": 4953.0,
                "executed_at_beijing": "2026-09-02T14:17:53+08:00",
                "linked_decision_id": decision_id,
                "execution_status": "EXECUTED",
            }), encoding="utf-8")
            with patch.object(reconciliation, "ROOT", root):
                result = reconciliation.build()
            self.assertEqual(result["status"], "RECONCILED")
            self.assertEqual(result["actionable_confirmation_count"], 0)
            match = next(item for item in result["matches"] if item["event_id"] == event_id)
            self.assertEqual(match["match_type"], "CONFIRMED_BY_TRADE_EVENT")


if __name__ == "__main__":
    unittest.main()
