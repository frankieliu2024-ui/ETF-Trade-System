from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "ETF_SYSTEM_INDEX.md"
CONTRACT = ROOT / "docs" / "Codex协作执行说明.md"
PROTOCOL = ROOT / "docs" / "生产变更与并发写入协议_V1.0.md"


class CodexCollaborationContractTests(unittest.TestCase):
    def test_index_routes_to_single_collaboration_contract(self):
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("docs/Codex协作执行说明.md", index)
        self.assertEqual(index.count("docs/Codex协作执行说明.md"), 1)

    def test_contract_persists_brief_and_result_on_github(self):
        text = CONTRACT.read_text(encoding="utf-8")
        for marker in (
            "CODEX_EXECUTION_BRIEF",
            "完整、可独立执行",
            "用户默认只需启动 Codex",
            "结果必须回写同一 Issue 或关联 PR",
            "ChatGPT 与【ETF变更复核】后续直接从 GitHub 读取",
            "latest main",
            "残余风险",
        ):
            self.assertIn(marker, text)
        self.assertIn("聊天记录不是唯一交接媒介", text)

    def test_contract_does_not_create_governance_or_task_state(self):
        text = CONTRACT.read_text(encoding="utf-8")
        for marker in ("第二治理规则源", "Task-to-Task 通信", "平行 workflow", "state"):
            self.assertIn(marker, text)
        protocol = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("准入只允许三个结果", protocol)
        self.assertIn("四维正交判定：准入、工作、集成与优先级", protocol)

    def test_production_merge_still_defers_to_v18(self):
        text = CONTRACT.read_text(encoding="utf-8")
        self.assertIn("V1.8", text)
        self.assertIn("不授予自动合并权限", text)
        self.assertIn("不降低任何测试", text)


if __name__ == "__main__":
    unittest.main()
