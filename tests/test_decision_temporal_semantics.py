import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_execution_quality import build as build_quality
from scripts.build_execution_reconciliation import parse_money
from scripts.decision_trade_link import resolve_link


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
