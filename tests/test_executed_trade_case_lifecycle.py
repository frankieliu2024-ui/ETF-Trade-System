import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_system_consistency as consistency
from scripts import build_execution_reconciliation as reconciliation


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


    def test_exact_linked_trade_before_decision_time_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events/decisions").mkdir(parents=True)
            (root / "events/trades").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            (root / "events/decisions/decision.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_id": "decision-159326",
                "decision_time_beijing": "2026-09-02T14:21:31+08:00",
                "market_date": "2026-09-02",
                "candidate_code": "159326",
                "candidate_name": "电网设备ETF",
                "formal_decision": {
                    "lifecycle": {"电网设备ETF（159326）": "Trial已执行"},
                    "amount_action": "电网设备ETF（159326）已于14:17:53以1.651元买入3000份，成交本金4953元。",
                },
            }), encoding="utf-8")
            (root / "events/trades/trade.json").write_text(json.dumps({
                "event_id": "trade-159326",
                "code": "159326",
                "side": "BUY",
                "quantity": 3000,
                "price": 1.651,
                "amount": 4953.0,
                "confirmed_at_beijing": "2026-09-02T14:17:53+08:00",
                "linked_decision_id": "decision-159326",
                "execution_status": "EXECUTED",
            }), encoding="utf-8")
            (root / "data/state/account_fact.json").write_text(json.dumps({
                "positions": [], "updated_at": "2026-09-02T21:05:00+08:00"
            }), encoding="utf-8")
            old = (reconciliation.ROOT, reconciliation.STATE, reconciliation.OUT)
            reconciliation.ROOT = root
            reconciliation.STATE = root / "data/state"
            reconciliation.OUT = reconciliation.STATE / "execution_reconciliation.json"
            try:
                result = reconciliation.build()
            finally:
                reconciliation.ROOT, reconciliation.STATE, reconciliation.OUT = old
            self.assertEqual(result["status"], "RECONCILED")
            self.assertEqual(result["actionable_count"], 0)
            self.assertEqual(result["matches"][0]["status"], "CONFIRMED_BY_TRADE_EVENT")
            self.assertEqual(result["matches"][0]["trade_event_ids"], ["trade-159326"])

if __name__ == "__main__":
    unittest.main()
