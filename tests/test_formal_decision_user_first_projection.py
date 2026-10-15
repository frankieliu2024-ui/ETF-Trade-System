from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "docs" / "Formal Decision执行与用户回复协议.md"


class FormalDecisionUserFirstProjectionTests(unittest.TestCase):
    def test_user_reply_precedes_formal_post_projection(self):
        text = FORMAL.read_text(encoding="utf-8")
        reply = text.index("首次用户可见回复立即输出同一份完整 FINAL_CONTENT")
        bds = text.index("用户回复后，将同一正文和同一请求身份写入现有 BUSINESS_DECISION_SOURCE")
        event = text.index("由既有 canonical owner 形成当前精确 Formal Decision event")
        self.assertLess(reply, bds)
        self.assertLess(bds, event)
        self.assertIn("不等待 BDS、completion、REPORT、notification、PushPlus 或 provider 成功", text)
        self.assertIn("BDS、event 或通知投影失败时保留当前请求和 FINAL_CONTENT", text)


if __name__ == "__main__":
    unittest.main()
