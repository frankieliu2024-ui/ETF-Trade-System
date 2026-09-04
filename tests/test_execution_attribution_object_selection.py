import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.decision_trade_link import decision_price_for_trade, resolve_link
from scripts import process_state_sync_request as state_sync
from scripts.lifecycle_state import build_lifecycle_projection


class ExecutionAttributionObjectSelectionTests(unittest.TestCase):
    def _decision(self):
        return {
            "event_type": "FORMAL_DECISION",
            "decision_id": "decision-ab",
            "decision_time_beijing": "2026-09-03T09:42:59+08:00",
            "candidate_code": "518880",
            "candidate_name": "黄金ETF",
            "hypothesis_id": "HYP_518880_20260903_xecution",
            "price_at_decision": 9.067,
            "formal_decision": {
                "lifecycle": "515880退出；518880观察",
                "amount_action": "515880卖出7400；518880新增买入0",
            },
            "comparison_snapshot": {
                "items": [
                    {"code": "515880", "price": 0.651},
                    {"code": "518880", "price": 9.067},
                ]
            },
        }

    def test_different_executed_object_does_not_use_candidate_price(self):
        decision = self._decision()
        self.assertIsNone(decision_price_for_trade(decision, {"code": "515880"}))
        self.assertEqual(decision_price_for_trade(decision, {"code": "518880"}), 9.067)

    def test_exit_a_candidate_b_is_partial_and_has_no_b_hypothesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "events" / "decisions"
            decisions.mkdir(parents=True)
            decision = self._decision()
            (decisions / "decision-ab.json").write_text(json.dumps(decision), encoding="utf-8")
            trade = {
                "code": "515880",
                "side": "SELL",
                "quantity": 7400,
                "price": 0.648,
                "confirmed_at_beijing": "2026-09-03T09:47:55+08:00",
            }
            with patch.object(state_sync, "ROOT", root):
                result = state_sync.execution_attribution(trade, "decision-ab")
            self.assertEqual(result["status"], "PARTIAL")
            self.assertIsNone(result["hypothesis_id"])
            self.assertIsNone(result["decision_price"])
            self.assertIsNone(result["adverse_execution_cost_pct"])
            self.assertEqual(result["decision_to_execution_seconds"], 296.0)

    def test_same_object_buy_path_preserves_candidate_price_and_hypothesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "events" / "decisions"
            decisions.mkdir(parents=True)
            decision = self._decision()
            decision["candidate_code"] = "518880"
            (decisions / "decision-ab.json").write_text(json.dumps(decision), encoding="utf-8")
            trade = {
                "code": "518880",
                "side": "BUY",
                "quantity": 500,
                "price": 9.109,
                "confirmed_at_beijing": "2026-09-03T11:21:04+08:00",
            }
            with unittest.mock.patch.object(state_sync, "ROOT", root):
                result = state_sync.execution_attribution(trade, "decision-ab")
            self.assertEqual(result["status"], "READY")
            self.assertEqual(result["decision_price"], 9.067)
            self.assertEqual(result["hypothesis_id"], "HYP_518880_20260903_xecution")

    def test_trade_before_decision_cannot_be_attributed_to_future_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "events" / "decisions"
            decisions.mkdir(parents=True)
            decision = self._decision()
            (decisions / "decision-ab.json").write_text(json.dumps(decision), encoding="utf-8")
            linked, _, status = resolve_link(root, {
                "code": "515880", "side": "SELL",
                "confirmed_at_beijing": "2026-09-03T09:40:00+08:00",
            }, "decision-ab")
            self.assertEqual(linked, "")
            self.assertEqual(status, "NO_PRIOR_MATCHING_DECISION")

    def test_explicit_per_action_price_is_allowed_without_candidate_substitution(self):
        decision = self._decision()
        decision["formal_decision"]["actions"] = [
            {"code": "515880", "side": "SELL", "decision_price": 0.651}
        ]
        self.assertEqual(decision_price_for_trade(decision, {"code": "515880"}), 0.651)


    def test_lifecycle_does_not_attach_exit_a_to_trial_candidate_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events" / "decisions").mkdir(parents=True)
            (root / "events" / "trades").mkdir(parents=True)
            (root / "config" / "market").mkdir(parents=True)
            (root / "data" / "state").mkdir(parents=True)
            (root / "config" / "market" / "a_share_trading_calendar_2026.json").write_text(
                json.dumps({"closed_dates": []}), encoding="utf-8"
            )
            (root / "data" / "state" / "CURRENT.json").write_text(
                json.dumps({"market_date": "2026-09-04"}), encoding="utf-8"
            )
            decision = self._decision()
            (root / "events" / "decisions" / "decision-ab.json").write_text(
                json.dumps(decision), encoding="utf-8"
            )
            (root / "events" / "trades" / "exit-a.json").write_text(json.dumps({
                "event_id": "exit-a", "linked_decision_id": "decision-ab",
                "code": "515880", "execution_status": "EXECUTED",
                "execution_date": "2026-09-03",
            }), encoding="utf-8")
            projection = build_lifecycle_projection(root)
            self.assertEqual(projection["active_lifecycles"], [])


if __name__ == "__main__":
    unittest.main()
