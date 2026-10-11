import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
    from scripts.process_state_sync_request import validate_formal_decision_contract
    from scripts.business_decision_source import _validate_single_frozen_presentation
except ModuleNotFoundError:
    from process_state_sync_request import validate_formal_decision_contract
    from business_decision_source import _validate_single_frozen_presentation


class FormalDecisionContractTests(unittest.TestCase):
    def test_formal_presentation_is_required_at_business_source_ingress(self):
        with self.assertRaisesRegex(ValueError, "presentation content is required"):
            _validate_single_frozen_presentation({}, required=True)

    def test_formal_presentation_must_be_identical_across_exposed_fields(self):
        content = "【ETF正式决策】2026-10-11 11:00 正式判断"
        with self.assertRaisesRegex(ValueError, "one identical frozen source"):
            _validate_single_frozen_presentation({
                "presentation_content": content,
                "decision_response": {"presentation_content": content + "（改写）"},
                "presentation_binding": {"full_content": content},
            })

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

    def test_unambiguous_rich_opportunity_status_is_accepted(self):
        self.assertEqual(validate_formal_decision_contract({
            "opportunity_status": "Trial持仓反馈显著增强；下一可交易节点进入Confirm复核",
        }), "")

    def test_multi_object_lifecycle_mapping_and_legacy_composite_text_are_accepted(self):
        # This test owns lifecycle syntax compatibility, not live account
        # membership. Use explicitly historical wording so the fixture remains
        # deterministic when production holdings change.
        error = validate_formal_decision_contract({
            "opportunity_status": "无机会",
            "lifecycle": {
                "半导体设备ETF（561980）": "历史持有管理",
                "黄金ETF（518880）": "历史持有管理；继续Trial验证",
                "通信ETF（515880）": "历史退出；原Trial假设已关闭",
            },
        })
        self.assertEqual(error, "")

    def test_legacy_historical_lifecycle_text_is_rejected_per_object(self):
        for lifecycle in ("持有并继续Trial验证", "保持已退出，本节点不重新开启"):
            error = validate_formal_decision_contract({
                "opportunity_status": "无机会",
                "lifecycle": {"通信ETF（515880）": lifecycle},
            })
            self.assertIn("lifecycle", error)

    def test_unregistered_lifecycle_text_is_rejected_per_object(self):
        error = validate_formal_decision_contract({
            "opportunity_status": "无机会",
            "lifecycle": {"半导体设备ETF（561980）": "需要重新理解"},
        })
        self.assertIn("registered lifecycle action", error)


if __name__ == "__main__":
    unittest.main()
