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
                experience.write_text("### 2.3 CASE-20260901-01：review\nTRADE_EVENT:trade-1\n", encoding="utf-8")
                reviews = root / "events" / "reviews"
                reviews.mkdir(parents=True)
                (reviews / "2026-09-01.json").write_text(json.dumps({
                    "review": {"case_mapping": {"primary": {
                        "trade_event_id": "trade-1",
                        "decision_id": "decision-1",
                        "case_id": "CASE-20260901-01",
                        "security_code": "",
                    }}}
                }), encoding="utf-8")
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

    def test_planned_amount_prefers_explicit_principal_over_price(self):
        self.assertEqual(
            reconciliation.parse_money("以1.651元买入3000份，成交本金4953元"),
            4953,
        )

    def test_exact_linked_trade_before_formal_decision_is_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision_dir = root / "events" / "decisions"
            trade_dir = root / "events" / "trades"
            decision_dir.mkdir(parents=True)
            trade_dir.mkdir(parents=True)
            decision_id = "20260902_141753_trade_159326"
            (decision_dir / f"{decision_id}.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_id": decision_id,
                "market_date": "2026-09-02",
                "decision_time_beijing": "2026-09-02T14:21:31+08:00",
                "candidate_code": "159326",
                "candidate_name": "电网设备ETF",
                "formal_decision": {
                    "lifecycle": "Trial已执行",
                    "amount_action": "买入3000份，成交价1.651元，成交金额4953元",
                },
            }), encoding="utf-8")
            event_id = "20260902_141753_trade_159326"
            (trade_dir / f"{event_id}.json").write_text(json.dumps({
                "event_id": event_id,
                "code": "159326",
                "side": "BUY",
                "quantity": 3000,
                "price": 1.651,
                "amount": 4953.0,
                "executed_at_beijing": "2026-09-02T14:17:53+08:00",
                "linked_decision_id": decision_id,
                "execution_status": "EXECUTED",
            }), encoding="utf-8")
            with patch.object(reconciliation, "ROOT", root):
                result = reconciliation.build()
            self.assertEqual(result["status"], "RECONCILED")
            self.assertEqual(result["actionable_count"], 0)
            match = next(item for item in result["matches"] if event_id in item["trade_event_ids"])
            self.assertEqual(match["status"], "CONFIRMED_BY_TRADE_EVENT")


    def test_candidate_does_not_create_buy_for_explicit_sell_decision(self):
        event = {
            "event_type": "FORMAL_DECISION",
            "decision_id": "sell-decision",
            "market_date": "2026-09-03",
            "decision_time_beijing": "2026-09-03T09:42:59+08:00",
            "candidate_code": "518880",
            "candidate_name": "黄金ETF",
            "formal_decision": {
                "lifecycle": "Trial观察候选",
                "amount_action": "通信ETF（515880）已卖出全部7,400份，成交金额4,795.20元；新增买入0元。",
            },
        }
        intents = reconciliation.decision_intents(event)
        self.assertEqual([(x["code"], x["side"]) for x in intents], [("515880", "SELL")])

    def test_explicit_buy_object_creates_buy_intent(self):
        event = {
            "event_type": "FORMAL_DECISION",
            "decision_id": "buy-decision",
            "market_date": "2026-09-03",
            "decision_time_beijing": "2026-09-03T11:17:54+08:00",
            "candidate_code": "518880",
            "candidate_name": "黄金ETF",
            "formal_decision": {
                "lifecycle": "黄金ETF进入Trial",
                "amount_action": "黄金ETF（518880）于11:21:04以9.109元买入500份，成交本金4,554.50元。",
            },
        }
        intents = reconciliation.decision_intents(event)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["code"], "518880")
        self.assertEqual(intents[0]["side"], "BUY")
        self.assertEqual(intents[0]["planned_amount_yuan"], 4554)

if __name__ == "__main__":
    unittest.main()
