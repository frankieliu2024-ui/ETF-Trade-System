import unittest

from scripts.business_decision_source import _normalize_formal_presentation_layout


class FormalContentBindingCanonicalizationTests(unittest.TestCase):
    def test_handoff_diagnostics_are_not_part_of_frozen_body(self):
        raw = ("【ETF正式决策】2026-10-21 17:20 正式判断\\n\\n"
               "【业务结论】\\n\\n现金胜出。\\n\\n"
               "最终交接状态：BDS、completion、PushPlus未确认。")
        frozen = _normalize_formal_presentation_layout(raw)
        self.assertNotIn("最终交接状态", frozen)

    def test_business_labels_are_canonicalized_before_hashing(self):
        raw = "【持仓动作】\\n\\nHOLD胜过REDUCE/EXIT；下一合法节点复核。"
        frozen = _normalize_formal_presentation_layout(raw)
        self.assertIn("继续持有优于减持或全部退出", frozen)
        self.assertIn("下一次正式判断将重新评估", frozen)
        self.assertNotIn("HOLD/REDUCE/EXIT", frozen)


if __name__ == "__main__":
    unittest.main()
