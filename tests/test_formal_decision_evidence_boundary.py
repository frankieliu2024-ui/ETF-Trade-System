import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "ETF_SYSTEM_INDEX.md"
FORMAL_PROTOCOL = ROOT / "docs" / "Formal Decision执行与用户回复协议.md"
MUTATION_PROTOCOL = ROOT / "docs" / "生产变更与并发写入协议_V1.0.md"
MUTATION_MIRROR = ROOT / "config" / "maintenance" / "production_mutation_protocol.json"


class FormalDecisionEvidenceBoundaryTests(unittest.TestCase):
    def test_ready_forbids_duplicate_formal_fact_refresh_but_allows_bounded_supplemental_research(self):
        index = INDEX.read_text(encoding="utf-8")
        protocol = FORMAL_PROTOCOL.read_text(encoding="utf-8")
        governance = MUTATION_PROTOCOL.read_text(encoding="utf-8")

        for text in (index, protocol, governance):
            self.assertIn("request-bound", text)
            self.assertIn("外部补充证据", text)
            self.assertIn("不得覆盖", text)
            self.assertIn("PIT/freshness", text)
            self.assertIn("不得机械重复查询", text)

        self.assertIn("不得重新生产、刷新、拉取或重建已经 request-bound 的正式行情／事实", index)
        self.assertIn("不得重新生产、刷新、拉取或重建已经 request-bound 的正式行情／事实", governance)
        self.assertIn("不重新生产、刷新、拉取或重建已经 request-bound 的正式行情／事实", protocol)
        self.assertIn("Bigdata、Longbridge、Exa", index)
        self.assertIn("不得阻塞已经具备的 READY 回复资格", protocol)

    def test_machine_governance_mirror_preserves_same_boundary(self):
        mirror = json.loads(MUTATION_MIRROR.read_text(encoding="utf-8"))
        rule = mirror["change_admission"]["formal_decision_reply_priority"]
        self.assertIn("不得重新生产、刷新、拉取或重建已经request-bound的正式行情/事实", rule)
        self.assertIn("决策相关外部补充证据", rule)
        self.assertIn("不得覆盖request-bound/canonical正式事实", rule)
        self.assertIn("不得机械重复查询", rule)
        self.assertIn("补证失败不得阻塞已经具备的READY回复资格", rule)
        self.assertIn("不新增状态、workflow或并行治理链", rule)


if __name__ == "__main__":
    unittest.main()
