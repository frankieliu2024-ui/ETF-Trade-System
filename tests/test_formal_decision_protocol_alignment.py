from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "ETF_SYSTEM_INDEX.md"
FORMAL = ROOT / "docs" / "Formal Decision执行与用户回复协议.md"


class FormalDecisionProtocolAlignmentTests(unittest.TestCase):
    def test_index_routes_bds_through_canonical_event(self):
        text = INDEX.read_text(encoding="utf-8")
        self.assertIn("BDS只负责业务事实和正文冻结，不是通知唤醒事件", text)
        self.assertIn("形成当前请求对应的唯一 Formal Decision event", text)
        self.assertNotIn("回读通过后才交给既有notification_center发起通知尝试", text)
        self.assertNotIn("不是用户回复前置节点", text)

    def test_ready_does_not_bypass_current_event_handoff(self):
        text = FORMAL.read_text(encoding="utf-8")
        self.assertIn("不得等待历史 completion、历史 REPORT", text)
        self.assertIn("必须完成当前请求的BDS交接和既有canonical Formal Decision event形成", text)
        self.assertNotIn("不得等待 completion、REPORT、notification、PushPlus 或系统健康状态", text)


if __name__ == "__main__":
    unittest.main()
