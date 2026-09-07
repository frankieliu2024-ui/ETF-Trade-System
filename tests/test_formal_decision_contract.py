import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
    from scripts.process_state_sync_request import validate_formal_decision_contract
except ModuleNotFoundError:
    from process_state_sync_request import validate_formal_decision_contract


class FormalDecisionContractTests(unittest.TestCase):
    def test_registered_opportunity_and_holding_values_are_accepted(self):
        self.assertEqual(validate_formal_decision_contract({
            "opportunity_status": "观察机会",
            "risk_permission": "允许Trial",
            "lifecycle": "持有管理",
        }), "")

    def test_invalid_opportunity_status_is_rejected_at_ingress(self):
        error = validate_formal_decision_contract({
            "opportunity_status": "当前无新的ETF机会达到Trial或Confirm标准",
        })
        self.assertIn("opportunity_status", error)

    def test_multi_object_lifecycle_mapping_and_legacy_composite_text_are_accepted(self):
        error = validate_formal_decision_contract({
            "opportunity_status": "无机会",
            "lifecycle": {
                "半导体设备ETF（561980）": "持有管理",
                "黄金ETF（518880）": "持有并继续Trial验证",
                "通信ETF（515880）": "保持已退出，本节点不重新开启",
            },
        })
        self.assertEqual(error, "")

    def test_unregistered_lifecycle_text_is_rejected_per_object(self):
        error = validate_formal_decision_contract({
            "opportunity_status": "无机会",
            "lifecycle": {"半导体设备ETF（561980）": "需要重新理解"},
        })
        self.assertIn("registered lifecycle action", error)


if __name__ == "__main__":
    unittest.main()
