import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_execution_quality import build as build_execution_quality
from scripts.build_execution_reconciliation import parse_money
from scripts.decision_trade_link import resolve_link
from scripts.process_state_sync_request import execution_attribution


class DecisionTemporalSemanticsTests(unittest.TestCase):
    def _root(self, decision=None, trade=None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "events" / "decisions").mkdir(parents=True)
        (root / "events" / "trades").mkdir(parents=True)
        if decision:
            (root / "events" / "decisions" / f"{decision['decision_id']}.json").write_text(
                json.dumps(decision, ensure_ascii=False), encoding="utf-8"
            )
        if trade:
            (root / "events" / "trades" / f"{trade['event_id']}.json").write_text(
                json.dumps(trade, ensure_ascii=False), encoding="utf-8"
            )
        return tmp, root

    def test_parser_prefers_trade_principal_over_price(self):
        self.assertEqual(parse_money("以1.651元买入3000份，成交本金4953元"), 4953)
        self.assertEqual(parse_money("成交金额4953元"), 4953)
        self.assertEqual(parse_money("5000元"), 5000)
        self.assertEqual(parse_money("5k"), 5000)

    def test_exact_async_link_has_priority_over_prior_match(self):
        decision = {
            "decision_id": "d-async", "decision_time_beijing": "2026-09-02T14:21:31+08:00",
            "recorded_at_beijing": "2026-09-02T14:21:31+08:00",
            "decision_effective_ordering": "BEFORE_EXECUTION",
            "timing_quality": "USER_CONFIRMED_BOUNDED",
            "formal_decision": {"amount_action": "买入 电网设备ETF（159326）"},
        }
        trade = {
            "event_id": "t", "linked_decision_id": "d-async", "confirmed_at_beijing": "2026-09-02T14:17:53+08:00",
            "code": "159326", "name": "电网设备ETF", "side": "BUY",
        }
        tmp, root = self._root(decision, trade)
        try:
            linked, _, status = resolve_link(root, trade, "d-async")
            self.assertEqual(linked, "d-async")
            self.assertEqual(status, "EXPLICIT_ASYNC_CANONICALIZATION")
        finally:
            tmp.cleanup()

    def test_quality_does_not_emit_negative_latency_for_bounded_ordering(self):
        decision = {
            "decision_id": "d-async", "decision_time_beijing": "2026-09-02T14:21:31+08:00",
            "recorded_at_beijing": "2026-09-02T14:21:31+08:00",
            "decision_effective_ordering": "BEFORE_EXECUTION",
            "timing_quality": "USER_CONFIRMED_BOUNDED",
            "candidate_code": "159326", "candidate_name": "电网设备ETF",
            "price_at_decision": 1.649,
            "formal_decision": {"amount_action": "买入 电网设备ETF（159326）"},
        }
        trade = {
            "event_id": "t", "linked_decision_id": "d-async", "confirmed_at_beijing": "2026-09-02T14:17:53+08:00",
            "code": "159326", "name": "电网设备ETF", "side": "BUY", "price": 1.651,
        }
        tmp, root = self._root(decision, trade)
        try:
            item = build_execution_quality(root)["items"][0]
            self.assertEqual(item["decision_id"], "d-async")
            self.assertIsNone(item["decision_to_execution_seconds"])
            self.assertEqual(item["timing_status"], "BOUNDED_BEFORE_EXECUTION")
            self.assertEqual(item["status"], "READY")
        finally:
            tmp.cleanup()

    def test_attribution_uses_effective_time_and_closed_bounded_ordering(self):
        decision = {
            "decision_id": "d", "decision_time_beijing": "2026-09-02T14:21:31+08:00",
            "decision_effective_ordering": "BEFORE_EXECUTION",
            "timing_quality": "USER_CONFIRMED_BOUNDED", "price_at_decision": 1.649,
        }
        trade = {"confirmed_at_beijing": "2026-09-02T14:17:53+08:00", "price": 1.651}
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            (root / "events" / "decisions").mkdir(parents=True)
            (root / "events" / "decisions" / "d.json").write_text(json.dumps(decision), encoding="utf-8")
            import scripts.process_state_sync_request as sync
            old_root = sync.ROOT
            sync.ROOT = root
            try:
                result = execution_attribution(trade, "d")
            finally:
                sync.ROOT = old_root
            self.assertEqual(result["timing_status"], "BOUNDED_BEFORE_EXECUTION")
            self.assertIsNone(result["decision_to_execution_seconds"])
        finally:
            tmp.cleanup()

    def test_legacy_true_execution_before_decision_is_not_accepted(self):
        decision = {
            "decision_id": "d", "decision_time_beijing": "2026-09-02T14:21:31+08:00",
            "formal_decision": {"amount_action": "买入 电网设备ETF（159326）"},
        }
        trade = {
            "event_id": "t", "linked_decision_id": "d", "confirmed_at_beijing": "2026-09-02T14:17:53+08:00",
            "code": "159326", "name": "电网设备ETF", "side": "BUY",
        }
        tmp, root = self._root(decision, trade)
        try:
            linked, _, status = resolve_link(root, trade, "d")
            self.assertEqual(linked, "")
            self.assertEqual(status, "NO_PRIOR_MATCHING_DECISION")
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
