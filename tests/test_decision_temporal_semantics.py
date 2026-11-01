import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_execution_quality import build as build_quality
from scripts.build_execution_reconciliation import parse_money
from scripts.decision_trade_link import resolve_link
import scripts.process_state_sync_request as state_sync


class DecisionTemporalSemanticsTests(unittest.TestCase):
    def test_parser_prefers_explicit_principal(self):
        self.assertEqual(parse_money("以1.651元买入3000份，成交本金4953元"), 4953)
        self.assertEqual(parse_money("成交金额4953元"), 4953)
        self.assertEqual(parse_money("5k"), 5000)

    def test_exact_bounded_link_precedes_prior_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "events/decisions"; t = root / "events/trades"
            d.mkdir(parents=True); t.mkdir(parents=True)
            decision = {
                "decision_id": "d", "decision_time_beijing": "2026-09-02T14:21:31+08:00",
                "recorded_at_beijing": "2026-09-02T14:21:31+08:00",
                "decision_effective_ordering": "BEFORE_EXECUTION",
                "timing_quality": "USER_CONFIRMED_BOUNDED",
                "formal_decision": {"amount_action": "买入 电网设备ETF（159326）"},
            }
            trade = {
                "event_id": "t", "linked_decision_id": "d",
                "confirmed_at_beijing": "2026-09-02T14:17:53+08:00",
                "code": "159326", "name": "电网设备ETF", "side": "BUY",
            }
            (d/"d.json").write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
            linked, _, status = resolve_link(root, trade, "d")
            self.assertEqual(linked, "d")
            self.assertEqual(status, "EXPLICIT_ASYNC_CANONICALIZATION")

    def test_quality_closes_review_case_mapping_ineligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade_dir = root / "events/trades"
            review_dir = root / "events/reviews"
            trade_dir.mkdir(parents=True)
            review_dir.mkdir(parents=True)
            trade = {
                "event_id": "BROKER_TRADE_20260924_095255_159981_BUY_2800",
                "execution_date": "2026-09-24",
                "confirmed_at_beijing": "2026-09-24T09:52:55+08:00",
                "linked_decision_id": "historically-unrecoverable",
                "code": "159981",
                "name": "能源化工ETF",
                "side": "BUY",
                "execution_status": "EXECUTED",
            }
            review = {
                "review": {
                    "case_mapping": {
                        "ineligible_executed_trades": [{
                            "trade_event_id": trade["event_id"],
                            "eligibility": "EXPLICIT_CANONICAL_INELIGIBILITY",
                            "reason": "no canonical case",
                        }]
                    }
                }
            }
            (trade_dir / "trade.json").write_text(json.dumps(trade), encoding="utf-8")
            (review_dir / "2026-09-24.json").write_text(json.dumps(review), encoding="utf-8")
            item = build_quality(root)["items"][0]
            self.assertEqual(item["status"], "INELIGIBLE_UNRECOVERABLE_TRADE")
            self.assertEqual(item["link_status"], "EXPLICIT_CANONICAL_INELIGIBILITY")

    def test_quality_blocks_unlinked_executed_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade_dir = root / "events/trades"
            trade_dir.mkdir(parents=True)
            trade = {
                "event_id": "future-unlinked-trade",
                "execution_date": "2026-11-02",
                "confirmed_at_beijing": "2026-11-02T09:30:00+08:00",
                "code": "159056",
                "name": "全指器械ETF",
                "side": "BUY",
                "execution_status": "EXECUTED",
            }
            (trade_dir / "trade.json").write_text(json.dumps(trade), encoding="utf-8")
            result = build_quality(root)
            self.assertEqual(result["unlinked_trade_count"], 1)
            self.assertEqual(result["attribution_backlog"]["status"], "OPEN")
            self.assertEqual(result["attribution_gate"]["status"], "BLOCKED")
            self.assertEqual(result["attribution_gate"]["unlinked_trade_ids"], ["future-unlinked-trade"])
    def test_trade_ingress_persists_pit_link_without_explicit_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision_dir = root / "events/decisions"
            decision_dir.mkdir(parents=True)
            decision = {
                "decision_id": "formal-before-trade",
                "decision_time_beijing": "2026-11-02T09:00:00+08:00",
                "recorded_at_beijing": "2026-11-02T09:00:00+08:00",
                "candidate_code": "159056",
                "candidate_name": "全指器械ETF",
                "formal_decision": {"amount_action": "买入 全指器械ETF（159056）"},
            }
            (decision_dir / "formal-before-trade.json").write_text(
                json.dumps(decision, ensure_ascii=False),
                encoding="utf-8",
            )
            trade = {
                "event_id": "screenshot-trade",
                "confirmed_at_beijing": "2026-11-02T09:05:00+08:00",
                "code": "159056",
                "name": "全指器械ETF",
                "side": "BUY",
            }
            prior_root = state_sync.ROOT
            state_sync.ROOT = root
            try:
                linked = state_sync.resolve_trade_linked_decision_id(trade)
            finally:
                state_sync.ROOT = prior_root
            self.assertEqual(linked, "formal-before-trade")

    def test_quality_never_emits_false_negative_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); d=root/"events/decisions"; t=root/"events/trades"
            d.mkdir(parents=True); t.mkdir(parents=True)
            decision={"decision_id":"d","decision_time_beijing":"2026-09-02T14:21:31+08:00","recorded_at_beijing":"2026-09-02T14:21:31+08:00","decision_effective_ordering":"BEFORE_EXECUTION","timing_quality":"USER_CONFIRMED_BOUNDED","candidate_code":"159326","candidate_name":"电网设备ETF","price_at_decision":1.649,"formal_decision":{"amount_action":"买入 电网设备ETF（159326）"}}
            trade={"event_id":"t","linked_decision_id":"d","confirmed_at_beijing":"2026-09-02T14:17:53+08:00","code":"159326","name":"电网设备ETF","side":"BUY","price":1.651}
            (d/"d.json").write_text(json.dumps(decision, ensure_ascii=False),encoding="utf-8")
            (t/"t.json").write_text(json.dumps(trade, ensure_ascii=False),encoding="utf-8")
            item=build_quality(root)["items"][0]
            self.assertEqual(item["decision_id"],"d")
            self.assertIsNone(item["decision_to_execution_seconds"])
            self.assertEqual(item["timing_status"],"BOUNDED_BEFORE_EXECUTION")
            self.assertEqual(item["status"],"READY")


if __name__ == "__main__":
    unittest.main()
