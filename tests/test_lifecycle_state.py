from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.lifecycle_state import build_lifecycle_projection


def write(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class LifecycleStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write(self.root, "config/market/a_share_trading_calendar_2026.json", {"closed_dates": []})
        write(self.root, "data/state/CURRENT.json", {"market_date": "2026-09-01"})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_trial(self, decision_date="2026-08-27", trade_date="2026-08-27", hypothesis="H1") -> None:
        write(self.root, f"events/decisions/{hypothesis}.json", {
            "decision_id": hypothesis, "decision_time_beijing": f"{decision_date}T09:56:49+08:00",
            "candidate_code": "515880", "candidate_name": "通信ETF", "hypothesis_id": hypothesis,
            "hypothesis_closed": False, "formal_decision": {"lifecycle": "通信ETF（515880）：Trial"},
        })
        write(self.root, f"events/trades/{hypothesis}.json", {
            "event_id": f"trade-{hypothesis}", "linked_decision_id": hypothesis,
            "hypothesis_id": hypothesis, "execution_status": "EXECUTED", "execution_date": trade_date,
            "code": "515880", "name": "通信ETF", "side": "BUY",
        })

    def test_515880_is_t3_on_2026_09_01_and_weekend_is_not_counted(self):
        self.add_trial()
        result = build_lifecycle_projection(self.root)
        item = result["active_lifecycles"][0]
        self.assertEqual(item["current_t_plus"], 3)
        self.assertEqual(item["mandatory_decision_market_date"], "2026-09-01")
        self.assertTrue(item["decision_due"])
        self.assertEqual(build_lifecycle_projection(self.root, "2026-08-31")["active_lifecycles"][0]["current_t_plus"], 2)

    def test_explicit_trial_permission_without_hypothesis_is_recovered_from_linked_buy(self):
        write(self.root, "events/decisions/159992.json", {
            "decision_id": "D159992",
            "decision_time_beijing": "2026-09-29T09:04:12+08:00",
            "candidate_code": "159992",
            "candidate_name": "创新药ETF",
            "hypothesis_id": "",
            "formal_decision": {"risk_permission": "允许Trial"},
        })
        write(self.root, "events/trades/159992-buy.json", {
            "event_id": "trade-159992-buy",
            "linked_decision_id": "D159992",
            "hypothesis_id": "",
            "execution_status": "EXECUTED",
            "execution_date": "2026-09-29",
            "code": "159992",
            "name": "创新药ETF",
            "side": "BUY",
            "quantity": 5800,
        })
        result = build_lifecycle_projection(self.root, "2026-11-03")
        self.assertEqual(len(result["active_lifecycles"]), 1)
        item = result["active_lifecycles"][0]
        self.assertEqual(item["security_code"], "159992")
        self.assertEqual(item["lifecycle_status"], "ACTIVE_TRIAL")
        self.assertTrue(item["decision_due"])
        self.assertTrue(item["overdue"])
        self.assertEqual(item["source_trade_event_id"], "trade-159992-buy")

    def test_decision_without_execution_does_not_start_real_lifecycle(self):
        write(self.root, "events/decisions/no-trade.json", {
            "decision_id": "no-trade", "decision_time_beijing": "2026-08-27T09:00:00+08:00",
            "candidate_code": "515880", "hypothesis_id": "H-no-trade",
            "formal_decision": {"lifecycle": "通信ETF（515880）：Trial"},
        })
        self.assertEqual(build_lifecycle_projection(self.root)["active_lifecycles"], [])

    def test_t3_resolution_is_not_due_again(self):
        self.add_trial()
        write(self.root, "events/decisions/confirm.json", {
            "decision_id": "confirm", "decision_time_beijing": "2026-08-31T10:00:00+08:00",
            "hypothesis_id": "H1", "hypothesis_closed": False,
            "formal_decision": {"lifecycle": "通信ETF（515880）：Confirm评估"},
        })
        item = build_lifecycle_projection(self.root)["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertFalse(item["decision_due"])

    def test_overdue_trial_consumes_latest_explicit_same_security_decision(self):
        self.add_trial(decision_date="2026-08-27", trade_date="2026-08-27", hypothesis="H-overdue")
        write(self.root, "events/decisions/latest-management.json", {
            "decision_id": "latest-management",
            "decision_time_beijing": "2026-09-03T10:00:00+08:00",
            "candidate_code": "515880",
            "candidate_name": "通信ETF",
            "formal_decision": {
                "lifecycle": "通信ETF（515880）持有管理，不追加。",
                "main_candidate": "通信ETF（515880）",
            },
        })
        result = build_lifecycle_projection(self.root, "2026-09-05")
        item = result["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertEqual(item["hypothesis_status"], "HOLDING_MANAGEMENT")
        self.assertEqual(item["resolution"]["resolution_reason"], "LATEST_FORMAL_DECISION_COVERAGE")
        self.assertEqual(item["resolution"]["decision_id"], "latest-management")
        self.assertFalse(item["decision_due"])

    def test_latest_security_mention_without_business_action_does_not_resolve_overdue_trial(self):
        self.add_trial(decision_date="2026-08-27", trade_date="2026-08-27", hypothesis="H-no-action")
        write(self.root, "events/decisions/mention-only.json", {
            "decision_id": "mention-only",
            "decision_time_beijing": "2026-09-03T10:00:00+08:00",
            "candidate_code": "515880",
            "formal_decision": {"lifecycle": "通信ETF（515880）"},
        })
        item = build_lifecycle_projection(self.root, "2026-09-05")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "ACTIVE_TRIAL")
        self.assertTrue(item["overdue"])

    def test_projection_survives_rebuild_and_exposes_sources(self):
        self.add_trial()
        result = build_lifecycle_projection(self.root, "2026-08-28")
        item = result["active_lifecycles"][0]
        self.assertEqual(item["source_decision_id"], "H1")
        self.assertEqual(item["source_trade_event_id"], "trade-H1")
        self.assertEqual(item["current_t_plus"], 1)
        self.assertEqual(result["next_mandatory_node"], "")

    def test_linked_buy_recorded_before_decision_can_be_resolved_by_complete_sell(self):
        self.add_trial(decision_date="2026-09-02", trade_date="2026-09-02", hypothesis="H159326")
        decision = self.root / "events/decisions/H159326.json"
        value = json.loads(decision.read_text(encoding="utf-8"))
        value["candidate_code"] = "159326"
        value["formal_decision"]["lifecycle"] = "电网设备ETF（159326）：Trial"
        decision.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        trade = self.root / "events/trades/H159326.json"
        buy = json.loads(trade.read_text(encoding="utf-8"))
        buy.update({"event_id": "buy-159326", "code": "159326", "side": "BUY", "linked_decision_id": "H159326", "hypothesis_id": "H159326", "confirmed_at_beijing": "2026-09-02T09:00:00+08:00", "quantity": 3000})
        trade.write_text(json.dumps(buy, ensure_ascii=False), encoding="utf-8")
        write(self.root, "events/trades/sell-159326.json", {
            "event_id": "sell-159326", "code": "159326", "side": "SELL", "quantity": 3000,
            "execution_status": "EXECUTED", "execution_date": "2026-09-10",
            "confirmed_at_beijing": "2026-09-10T14:42:00+08:00",
        })
        item = build_lifecycle_projection(self.root, "2026-09-14")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertFalse(item["decision_due"])

    def test_nested_formal_candidate_code_can_be_resolved_by_complete_sell(self):
        write(self.root, "events/reviews/nested-formal.json", {
            "event_type": "FORMAL_DECISION_REPORT",
            "decision_id": "nested-159217",
            "decision_time_beijing": "2026-08-27T09:00:00+08:00",
            "formal_decision": {
                "lifecycle": "允许Trial",
                "candidate_code": "159217",
                "candidate_name": "港股通创新药ETF",
                "main_candidate": "159217 Trial评估成立",
            },
        })
        write(self.root, "events/trades/nested-buy.json", {
            "event_id": "nested-buy", "linked_decision_id": "nested-159217",
            "execution_status": "EXECUTED", "execution_date": "2026-08-27",
            "code": "159217", "side": "BUY", "quantity": 4400,
        })
        write(self.root, "events/trades/nested-sell.json", {
            "event_id": "nested-sell", "linked_decision_id": "other-decision",
            "execution_status": "EXECUTED", "execution_date": "2026-09-02",
            "confirmed_at_beijing": "2026-09-02T10:00:00+08:00",
            "code": "159217", "side": "SELL", "quantity": 4400,
        })
        item = build_lifecycle_projection(self.root, "2026-09-05")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertEqual(item["resolution"]["resolution_reason"], "CONFIRMED_COMPLETE_SELL")

    def test_completion_linked_trial_without_hypothesis_is_due_and_overdue(self):
        request_id = "formal-completion-generic-trial"
        write(self.root, f"requests/live_snapshot/{request_id}__scheduled_review_completion.json", {
            "request_id": request_id,
            "report_type": "ETF_FORMAL_DECISION",
            "requested_at_beijing": "2026-10-26T15:20:00+08:00",
            "final_content": "【机会ETF逐只评估】\\n1. 通用试探ETF（159057）：Trial评估成立。",
        })
        write(self.root, "events/trades/completion-buy-159057.json", {
            "event_id": "completion-buy-159057",
            "code": "159057",
            "name": "通用试探ETF",
            "side": "BUY",
            "execution_status": "EXECUTED",
            "execution_date": "2026-10-26",
            "linked_decision_id": f"FORMAL_COMPLETION:{request_id}",
            "quantity": 100,
        })
        result = build_lifecycle_projection(self.root, "2026-11-02")
        self.assertEqual(len(result["active_lifecycles"]), 1)
        item = result["active_lifecycles"][0]
        self.assertEqual(item["source_decision_id"], f"FORMAL_COMPLETION:{request_id}")
        self.assertIsNone(item["hypothesis_id"])
        self.assertTrue(item["decision_due"])
        self.assertTrue(item["overdue"])
        self.assertEqual(item["next_lifecycle_node"], "TRADE_MANDATORY_LIFECYCLE_NODE:T+3")

    def test_partial_sell_does_not_resolve_linked_trial(self):
        self.add_trial(hypothesis="H-partial")
        write(self.root, "events/trades/sell-partial.json", {
            "event_id": "sell-partial", "code": "515880", "side": "SELL", "quantity": 1,
            "execution_status": "EXECUTED", "execution_date": "2026-08-29",
        })
        item = build_lifecycle_projection(self.root, "2026-09-14")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "ACTIVE_TRIAL")


    def test_completion_exit_requires_unique_linked_lot(self):
        from scripts.lifecycle_state import _completion_explicit_exit
        decision = {"decision_id": "FORMAL_COMPLETION:R1", "candidate_code": "159057",
                    "_linked_execution_id": "buy1"}
        buy = {"event_id": "buy1", "linked_decision_id": "FORMAL_COMPLETION:R1",
               "code": "159057", "side": "BUY", "quantity": 100, "execution_date": "2026-10-26"}
        unrelated = {"event_id": "sell-other", "linked_decision_id": "OTHER",
                     "code": "159057", "side": "SELL", "quantity": 100}
        partial = {"event_id": "sell1", "linked_decision_id": "FORMAL_COMPLETION:R1",
                   "code": "159057", "side": "SELL", "quantity": 40, "execution_date": "2026-10-29"}
        self.assertIsNone(_completion_explicit_exit(self.root, decision, [buy, unrelated]))
        self.assertIsNone(_completion_explicit_exit(self.root, decision, [buy, partial]))
        self.assertIsNone(_completion_explicit_exit(self.root, decision, [buy, buy, partial]))
        complete = dict(partial, quantity=100)
        result = _completion_explicit_exit(self.root, decision, [buy, complete])
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["resolution_event_ids"], ["sell1"])

    def test_observation_only_candidate_with_trial_permission_is_not_trial(self):
        write(self.root, "events/decisions/observation-only.json", {
            "decision_id": "observation-only", "candidate_code": "518880",
            "candidate_name": "黄金ETF", "hypothesis_id": "H518880",
            "formal_decision": {
                "risk_permission": "允许Trial",
                "lifecycle": "通信ETF（515880）原Trial退出；黄金ETF（518880）继续观察。",
                "main_candidate": "黄金ETF（518880）—观察，尚未达到执行。",
                "amount_action": "新增买入0元",
            },
        })
        write(self.root, "events/trades/observation-only-buy.json", {
            "event_id": "observation-only-buy", "linked_decision_id": "observation-only",
            "hypothesis_id": "H518880", "execution_status": "EXECUTED",
            "execution_date": "2026-09-03", "code": "518880", "side": "BUY", "quantity": 500,
        })
        self.assertEqual(build_lifecycle_projection(self.root)["active_lifecycles"], [])

    def test_completion_trial_accepts_explicit_followup_decision(self):
        request_id = "formal-completion-followup"
        write(self.root, f"requests/live_snapshot/{request_id}__scheduled_review_completion.json", {
            "request_id": request_id, "report_type": "ETF_FORMAL_DECISION",
            "requested_at_beijing": "2026-10-26T15:20:00+08:00",
            "final_content": "【机会ETF逐只评估】\n1. 通用试探ETF（159057）：Trial评估成立。",
        })
        opening_id = "completion-buy-159057"
        write(self.root, "events/trades/completion-buy-159057.json", {
            "event_id": opening_id, "code": "159057", "name": "通用试探ETF",
            "side": "BUY", "execution_status": "EXECUTED", "execution_date": "2026-10-26",
            "linked_decision_id": f"FORMAL_COMPLETION:{request_id}", "quantity": 100,
        })
        write(self.root, "events/decisions/completion-followup.json", {
            "decision_id": "completion-followup", "candidate_code": "159057",
            "linked_trade_event_id": opening_id, "decision_time_beijing": "2026-10-30T10:00:00+08:00",
            "formal_decision": {"lifecycle": "通用试探ETF（159057）Confirm评估"},
        })
        item = build_lifecycle_projection(self.root, "2026-11-03")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertEqual(item["hypothesis_status"], "CONFIRM_EVALUATION")
        self.assertEqual(item["resolution"]["decision_id"], "completion-followup")

    def test_completion_trial_resolves_from_later_linked_complete_sell(self):
        request_id = "formal-completion-sell-fallback"
        write(self.root, f"requests/live_snapshot/{request_id}__scheduled_review_completion.json", {
            "request_id": request_id, "report_type": "ETF_FORMAL_DECISION",
            "requested_at_beijing": "2026-10-26T15:20:00+08:00",
            "final_content": "【机会ETF逐只评估】\n1. 通用试探ETF（159057）：Trial评估成立。",
        })
        opening_id = "completion-buy-fallback-159057"
        write(self.root, "events/trades/completion-buy-fallback-159057.json", {
            "event_id": opening_id, "code": "159057", "name": "通用试探ETF",
            "side": "BUY", "execution_status": "EXECUTED", "execution_date": "2026-10-26",
            "linked_decision_id": f"FORMAL_COMPLETION:{request_id}", "quantity": 100,
        })
        write(self.root, "events/trades/completion-sell-fallback-159057.json", {
            "event_id": "completion-sell-fallback-159057", "code": "159057",
            "side": "SELL", "execution_status": "EXECUTED", "execution_date": "2026-10-29",
            "linked_decision_id": "FORMAL_COMPLETION:later-completion", "quantity": 100,
        })
        item = build_lifecycle_projection(self.root, "2026-11-03")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertEqual(item["resolution"]["resolution_reason"], "CONFIRMED_COMPLETE_SELL")
        self.assertEqual(item["resolution"]["resolution_event_ids"], ["completion-sell-fallback-159057"])


    def test_empty_hypothesis_does_not_match_other_decisions(self):
        from scripts.lifecycle_state import _resolution
        write(self.root, "events/decisions/unrelated.json", {
            "decision_id": "unrelated", "decision_time_beijing": "2026-09-30T10:00:00+08:00",
            "hypothesis_closed": True, "formal_decision": {"lifecycle": "退出"},
        })
        trial = {"decision_id": "trial", "candidate_code": "159992",
                 "hypothesis_id": "", "decision_time_beijing": "2026-09-29T09:00:00+08:00"}
        self.assertIsNone(_resolution(self.root, trial, __import__("datetime").date(2026, 10, 9)))


    def test_unlinked_sell_cannot_close_trial_with_other_same_symbol_buy(self):
        self.add_trial(hypothesis="H-multi")
        write(self.root, "events/trades/H-multi.json", {
            "event_id": "buy-main", "linked_decision_id": "H-multi",
            "hypothesis_id": "H-multi", "execution_status": "EXECUTED",
            "execution_date": "2026-08-27", "code": "515880",
            "side": "BUY", "quantity": 100,
        })
        write(self.root, "events/trades/other-buy.json", {
            "event_id": "buy-other", "linked_decision_id": "OTHER",
            "execution_status": "EXECUTED", "execution_date": "2026-08-28",
            "code": "515880", "side": "BUY", "quantity": 100,
        })
        write(self.root, "events/trades/unlinked-sell.json", {
            "event_id": "sell-unlinked", "execution_status": "EXECUTED",
            "execution_date": "2026-09-10", "code": "515880",
            "side": "SELL", "quantity": 100,
        })
        item = build_lifecycle_projection(self.root, "2026-09-14")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "ACTIVE_TRIAL")

    def test_explicitly_linked_sell_can_close_trial_despite_other_buy(self):
        self.test_unlinked_sell_cannot_close_trial_with_other_same_symbol_buy()
        sell_path = self.root / "events/trades/unlinked-sell.json"
        sell = json.loads(sell_path.read_text(encoding="utf-8"))
        sell["linked_decision_id"] = "H-multi"
        sell_path.write_text(json.dumps(sell), encoding="utf-8")
        item = build_lifecycle_projection(self.root, "2026-09-14")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")


    def test_explicit_exit_not_blocked_by_unrelated_same_symbol_sale(self):
        self.test_explicitly_linked_sell_can_close_trial_despite_other_buy()
        write(self.root, "events/trades/foreign-sell.json", {
            "event_id": "foreign-sell", "linked_decision_id": "OTHER",
            "execution_status": "EXECUTED", "execution_date": "2026-09-11",
            "code": "515880", "side": "SELL", "quantity": 100,
        })
        item = build_lifecycle_projection(self.root, "2026-09-14")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertEqual(item["resolution"]["resolution_event_ids"], ["sell-unlinked"])

    def test_unique_trial_lot_can_close_when_later_formal_decision_owns_sell(self):
        self.add_trial(decision_date="2026-09-02", trade_date="2026-09-02", hypothesis="H-later")
        decision = self.root / "events/decisions/H-later.json"
        value = json.loads(decision.read_text(encoding="utf-8"))
        value["candidate_code"] = "159326"
        value["formal_decision"]["lifecycle"] = "电网设备ETF（159326）：Trial"
        decision.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        trade = self.root / "events/trades/H-later.json"
        buy = json.loads(trade.read_text(encoding="utf-8"))
        buy.update({"event_id": "buy-later", "code": "159326", "side": "BUY",
                    "quantity": 3000, "linked_decision_id": "H-later",
                    "hypothesis_id": "H-later"})
        trade.write_text(json.dumps(buy, ensure_ascii=False), encoding="utf-8")
        write(self.root, "events/trades/sell-later.json", {
            "event_id": "sell-later", "code": "159326", "side": "SELL",
            "quantity": 3000, "execution_status": "EXECUTED",
            "execution_date": "2026-09-10",
            "linked_decision_id": "LATER_FORMAL_DECISION",
        })
        item = build_lifecycle_projection(self.root, "2026-09-14")["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertEqual(item["resolution"]["resolution_event_ids"], ["sell-later"])


    def test_sell_only_decision_cannot_create_trial_lifecycle(self):
        write(self.root, "events/decisions/sell-only.json", {
            "event_type": "FORMAL_DECISION",
            "decision_id": "sell-only",
            "decision_time_beijing": "2026-09-01T14:29:54+08:00",
            "candidate_code": "561980",
            "candidate_name": "半导体设备ETF",
            "hypothesis_id": "",
            "risk_permission": "允许Trial",
            "formal_decision": {
                "lifecycle": "Trial",
                "main_candidate": "半导体设备ETF（561980）",
            },
        })
        write(self.root, "events/trades/sell-only.json", {
            "event_id": "sell-only",
            "code": "561980",
            "name": "半导体设备ETF",
            "side": "SELL",
            "quantity": 13000,
            "execution_status": "EXECUTED",
            "execution_date": "2026-09-01",
            "linked_decision_id": "sell-only",
        })
        projection = build_lifecycle_projection(self.root, "2026-09-10")
        self.assertEqual(projection["active_lifecycles"], [])


if __name__ == "__main__":
    unittest.main()

