from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "ETF规则_MASTER.md"
FORMAL = ROOT / "docs" / "Formal Decision执行与用户回复协议.md"


class FormalDecisionZeroAmountCapitalContractTests(unittest.TestCase):
    def test_master_already_requires_four_zero_amount_decision_elements(self):
        master = MASTER.read_text(encoding="utf-8")
        for phrase in (
            "当前最接近可执行的候选",
            "仍缺少的决定性证据",
            "最可能替代的当前资本用途",
            "最近复核节点",
            "现金仍是下一单位资本的最有效用途",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, master)

    def test_formal_decision_protocol_carries_master_zero_amount_contract(self):
        formal = FORMAL.read_text(encoding="utf-8")
        required = (
            "0元决策的资本竞争完整性要求",
            "最近可执行候选",
            "决定性证据缺口",
            "资本替代路径",
            "最近复核节点",
            "本次不存在合理的最近可执行候选",
            "现金是否为当前下一单位资本的最有效用途",
            "风险许可禁止新增部署时，也不得跳过候选扫描、持仓复核或资本竞争",
            "旧仓卖出与新机会买入必须分别决议",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, formal)

    def test_candidate_identity_and_trade_authority_are_preserved(self):
        formal = FORMAL.read_text(encoding="utf-8")
        self.assertIn("不得把Discovery或观察ETF直接升级为机会ETF、Trial或Confirm", formal)
        self.assertIn("资本替代比较不等于卖出决议", formal)
        self.assertIn("不得预授权卖出", formal)
        self.assertIn("不构成新增固定评分、排序、机会身份、交易许可", formal)

    def test_contract_does_not_create_a_new_output_section_or_bds_dependency(self):
        formal = FORMAL.read_text(encoding="utf-8")
        self.assertNotIn("## 0元决策的资本竞争完整性要求", formal)
        self.assertIn("不新增独立输出栏目或机会状态", formal)
        self.assertIn("不构成新增固定评分、排序、机会身份、交易许可或独立输出章节", formal)
        self.assertIn("历史 BDS、旧完成记录", formal)
        # Formal Decision must continue to use its existing execution contract,
        # not the retired BDS business-decision chain.
        self.assertIn("不得创建、读取或依赖 BDS", formal)


if __name__ == "__main__":
    unittest.main()
