from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts import notification_center as center


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "ETF主动通知体系.md"


class NotificationSsotCloseoutTests(unittest.TestCase):
    def test_document_is_the_single_notification_rule_source(self):
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("唯一规范性规则来源", text)
        self.assertIn("ETF_SYSTEM_INDEX.md", text)
        self.assertIn("ETF规则_MASTER.md", text)
        self.assertEqual(text.count("## 10. "), 1)
        numbered = [int(n) for n in re.findall(r"^## (\d+)\.", text, re.MULTILINE)]
        self.assertEqual(len(numbered), len(set(numbered)))
        self.assertEqual(numbered[-3:], [10, 11, 12])

    def test_accepted_template_families_are_registered(self):
        text = SPEC.read_text(encoding="utf-8")
        for family in (
            "【市场异动】", "【收盘总结】", "【观察机会】", "【Trial机会】",
            "【Confirm机会】", "【机会失效】", "【持仓动作】", "【风险许可】",
            "【成交确认】", "【账户确认】", "【系统阻塞】", "【判断恢复】", "【收盘账户】",
        ):
            self.assertIn(family, text)

    def test_document_matches_renderer_and_boundaries(self):
        text = SPEC.read_text(encoding="utf-8")
        for phrase in ("INTERRUPT", "REPORT", "FULL_REPORT", "UNLINKED_TRADE_REQUIRES_ATTRIBUTION",
                       "decision_trigger", "用户如需交易必须人工核对并下单", "不生成新的交易指令"):
            self.assertIn(phrase, text)
        representative = [
            ("PENDING_EXECUTION_CONFIRMATION", "成交确认"),
            ("ACCOUNT_FACT_CONFIRMATION", "账户确认"),
            ("E2E_RECOVERED", "判断恢复"),
            ("SYSTEM_EVENT", "系统阻塞"),
        ]
        for event_type, family in representative:
            self.assertEqual(center.canonical_template_family({"event_type": event_type}), family)
        self.assertIsNone(center.canonical_template_family({"event_type": "UNKNOWN_FREE_FORM"}))

    def test_report_and_interrupt_contracts_are_distinct(self):
        report = {"event_type": "REPORT_DELIVERY_REQUEST", "full_content": "# 原文"}
        self.assertEqual(center.canonical_template_family(report), "REPORT")
        self.assertIs(center.render_canonical_notification(report), report)
        rendered = center.render_canonical_notification({
            "event_type": "PENDING_EXECUTION_CONFIRMATION",
            "security_code": "518880",
            "security_name": "黄金ETF",
            "confirmation_context": {},
        })
        self.assertIn("仅确认既有成交归因，不生成新交易指令。", rendered["content"])


if __name__ == "__main__":
    unittest.main()
