import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from process_state_sync_request import validate_managed_position_review_contract


class ManagedPositionReviewContractTests(unittest.TestCase):
    def setUp(self):
        self.account = {"status": "READY", "positions": [
            {"code": "561980", "name": "半导体设备ETF", "quantity": 100},
            {"code": "588000", "name": "科创50ETF", "quantity": 200},
        ]}

    def review(self, code, action="持有管理", changed=False):
        item = {
            "security_code": code,
            "current_action": action,
            "holding_state_risk_reward_evidence": "PIT持仓状态与风险收益证据",
            "holding_thesis_status": "原持有依据当前仍有效",
            "risk_reduction_or_exit_condition": "当前未达到降低风险或退出条件",
            "higher_efficiency_alternative": "当前不存在已经独立成立且效率更高的资本用途",
            "capital_occupancy_reason": "继续占用资本在当前风险收益与替代用途比较下仍合理",
            "capital_use": {
                "continued_holding_vs_cash": "继续持有与现金用途比较",
                "qualified_alternative": "无合格替代机会"},
            "action_changes_now": changed,
            "next_change_condition": "下一正式节点重新比较",
        }
        if action in {"降低风险", "退出"}:
            item.update(action_detail="卖出100份", capital_destination="现金")
        return item

    def test_20260914_1120_generic_lifecycle_is_rejected(self):
        with patch("process_state_sync_request.ROOT", Path(".")):
            error = validate_managed_position_review_contract(None, self.account)
        self.assertIn("non-empty list", error)

    def test_valid_hold_and_explicit_reduce_exit_are_accepted(self):
        with patch("process_state_sync_request.ROOT", Path(".")):
            self.assertEqual(validate_managed_position_review_contract(
                [self.review("561980"), self.review("588000")], self.account), "")
            self.assertEqual(validate_managed_position_review_contract(
                [self.review("561980", "降低风险", True), self.review("588000", "退出", True)], self.account), "")

    def test_missing_and_ambiguous_or_duplicate_object_are_rejected(self):
        with patch("process_state_sync_request.ROOT", Path(".")):
            error = validate_managed_position_review_contract([self.review("561980")], self.account)
            self.assertIn("missing", error)
            error = validate_managed_position_review_contract(
                [self.review("561980"), self.review("561980")], self.account)
            self.assertIn("exactly once", error)


    def test_missing_sell_chain_evidence_fields_are_rejected(self):
        required_fields = [
            "holding_thesis_status",
            "risk_reduction_or_exit_condition",
            "higher_efficiency_alternative",
            "capital_occupancy_reason",
        ]
        for field in required_fields:
            item = self.review("561980")
            item.pop(field)
            with self.subTest(field=field), patch("process_state_sync_request.ROOT", Path(".")):
                error = validate_managed_position_review_contract(
                    [item, self.review("588000")], self.account
                )
            self.assertIn(field, error)

    def test_hold_requires_explicit_capital_occupancy_reason(self):
        item = self.review("561980")
        item["capital_occupancy_reason"] = ""
        with patch("process_state_sync_request.ROOT", Path(".")):
            error = validate_managed_position_review_contract(
                [item, self.review("588000")], self.account
            )
        self.assertIn("capital_occupancy_reason", error)

    def test_review_does_not_infer_sell_from_trend(self):
        item = self.review("561980")
        item["holding_state_risk_reward_evidence"] = "下跌，但未形成退出结论"
        with patch("process_state_sync_request.ROOT", Path(".")):
            self.assertEqual(validate_managed_position_review_contract(
                [item, self.review("588000")], self.account), "")


if __name__ == "__main__":
    unittest.main()
