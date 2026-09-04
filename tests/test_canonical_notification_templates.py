from __future__ import annotations

import unittest

from scripts import notification_center as center


class CanonicalNotificationTemplateTests(unittest.TestCase):
    def event(self, event_type, **kwargs):
        event = {
            "event_type": event_type,
            "security_code": "518880",
            "security_name": "黄金ETF",
            "content": "结构化事实说明",
            "user_action": "查看正式事实",
            "confirmation_context": {},
        }
        event.update(kwargs)
        return event

    def test_registered_interrupt_events_map_to_unique_families(self):
        cases = [
            ("PENDING_EXECUTION_CONFIRMATION", "成交确认"),
            ("ACCOUNT_FACT_CONFIRMATION", "账户确认"),
            ("E2E_RECOVERED", "判断恢复"),
            ("SYSTEM_EVENT", "系统阻塞"),
            ("收盘账户", "收盘账户"),
        ]
        for event_type, family in cases:
            self.assertEqual(center.canonical_template_family(self.event(event_type)), family)

    def test_formal_decision_status_families(self):
        for status, family in [
            ("观察机会", "观察机会"),
            ("Trial机会", "Trial机会"),
            ("Confirm机会", "Confirm机会"),
            ("无机会", "机会失效"),
        ]:
            event = self.event(
                "FORMAL_DECISION_MATERIAL_CHANGE",
                confirmation_context={
                    "security_code": "518880",
                    "security_name": "黄金ETF",
                    "opportunity_status": status,
                    "previous_opportunity_status": "观察机会",
                    "risk_permission": "允许Trial",
                    "previous_risk_permission": "允许Trial",
                },
            )
            self.assertEqual(center.canonical_template_family(event), family)

    def test_unlinked_trade_uses_fixed_confirmation_boundary(self):
        rendered = center.render_canonical_notification(self.event("PENDING_EXECUTION_CONFIRMATION"))
        self.assertEqual(rendered["title"], "【成交确认】黄金ETF（518880）")
        self.assertIn("### 发生了什么", rendered["content"])
        self.assertIn("### 成交事实与缺口", rendered["content"])
        self.assertIn("仅确认既有成交及其归因，不生成新交易指令。", rendered["content"])

    def test_formal_opportunity_has_fixed_sections_and_manual_execution_boundary(self):
        event = self.event(
            "FORMAL_DECISION_MATERIAL_CHANGE",
            confirmation_context={
                "security_code": "518880",
                "security_name": "黄金ETF",
                "opportunity_status": "Trial机会",
                "previous_opportunity_status": "观察机会",
                "risk_permission": "允许Trial",
                "previous_risk_permission": "允许Trial",
            },
        )
        rendered = center.render_canonical_notification(event)
        self.assertEqual(rendered["title"], "【Trial机会】黄金ETF（518880）")
        for section in ("### 发生了什么", "### 当前正式状态", "### 为什么现在值得关注", "### 你需要做什么"):
            self.assertIn(section, rendered["content"])
        self.assertIn("用户如需交易必须人工核对并下单", rendered["content"])

    def test_raw_risk_trigger_is_not_a_parallel_notification(self):
        self.assertIsNone(center.decision_event())

    def test_unknown_event_fails_closed(self):
        self.assertIsNone(center.render_canonical_notification(self.event("UNKNOWN_FREE_FORM")))

    def test_report_is_full_forwarded_and_not_rewritten(self):
        event = self.event(
            "REPORT_DELIVERY_REQUEST",
            title="【交易复盘】完整原文",
            content="# 正式报告\n\n不得改写",
            full_content="# 正式报告\n\n不得改写",
            notification_channel="REPORT",
            delivery_mode="FULL_REPORT",
        )
        self.assertIs(center.render_canonical_notification(event), event)

    def test_independent_recovery_maps_to_judgment_recovery(self):
        rendered = center.render_canonical_notification(self.event("E2E_RECOVERED"))
        self.assertEqual(rendered["title"], "【判断恢复】此前暂缓的正式判断可以继续")
        self.assertIn("不生成新的交易指令", rendered["content"])


if __name__ == "__main__":
    unittest.main()
