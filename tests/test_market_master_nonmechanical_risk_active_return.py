from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "ETF规则_MASTER.md"
DASHBOARD = ROOT / "ETF当前状态_DASHBOARD.md"


class MasterNonmechanicalRiskActiveReturnTests(unittest.TestCase):
    def test_master_is_v229(self):
        text = MASTER.read_text(encoding="utf-8")
        self.assertIn("ETF波段交易系统 V2.2.29 规则 MASTER", text)
        self.assertIn("非机械风险边界与主动收益加速版", text)

    def test_risk_boundaries_are_review_only(self):
        text = MASTER.read_text(encoding="utf-8")
        self.assertIn("-5%、-8%、-10%只作为风险复核边界，不直接映射最终风险许可", text)
        self.assertIn("不得仅因阈值穿越机械切换许可", text)
        self.assertNotIn("风险率决定基础风险区间和基础权限", text)
        self.assertNotIn("Confirm关闭；恢复条件同时满足时只评估一次5,000元Trial", text)
        self.assertNotIn("禁止普通新增和Confirm；极端修复条件同时满足时只评估一次5,000元Trial", text)

    def test_dashboard_does_not_freeze_old_risk_permission_semantics(self):
        text = DASHBOARD.read_text(encoding="utf-8")
        self.assertNotIn("ETF策略风险口径（V2.2.18）", text)
        self.assertNotIn("风险观察区允许评估Trial但Confirm关闭", text)
        self.assertIn("Dashboard不根据风险观察区自行关闭Trial或Confirm", text)
        self.assertIn("当前风险许可与金额边界只读取最新MASTER和正式review", text)

    def test_fixed_position_anchors_are_removed(self):
        text = MASTER.read_text(encoding="utf-8")
        for phrase in ("恐慌期40%—60%", "修复期60%—80%", "趋势期80%—100%", "基础单只权重20%—25%", "观察最多35%", "观察最低15%"):
            self.assertNotIn(phrase, text)
        self.assertIn("市场状态只决定风险背景和风险复核强度，不对应固定仓位百分比", text)

    def test_trial_count_and_failure_pause_are_not_hard_gates(self):
        text = MASTER.read_text(encoding="utf-8")
        self.assertNotIn("同日最多两个独立Trial、合计不超过10,000元", text)
        self.assertNotIn("连续两个交易日同逻辑Trial失败后暂停新增", text)
        self.assertIn("不同独立假设不设置机械日内Trial数量或合计金额上限", text)
        self.assertIn("连续失败不产生固定暂停期", text)

    def test_active_return_acceleration_does_not_create_new_state(self):
        text = MASTER.read_text(encoding="utf-8")
        self.assertIn("主动收益加速原则", text)
        self.assertIn("证据已经足以形成正式决议", text)
        self.assertIn("可用现金不足时，不把“没现金”机械写成0元", text)
        self.assertIn("不新增风险指标、缓冲区、滞回状态、评分、许可层级", text)


if __name__ == "__main__":
    unittest.main()
