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
        self.assertIn("### 当前正式动作", rendered["content"])
        self.assertIn("### 下一关注点", rendered["content"])

    def test_risk_permission_uses_before_after_and_does_not_nest_producer_markdown(self):
        event = self.event(
            "FORMAL_DECISION_MATERIAL_CHANGE",
            content="### 发生了什么\n错误的producer markdown不应被重复包裹",
            confirmation_context={
                "security_code": "",
                "security_name": "",
                "opportunity_status": "观察机会",
                "previous_opportunity_status": "观察机会",
                "risk_permission": "允许Trial",
                "previous_risk_permission": "禁止新增",
                "change_summary": ["风险许可：禁止新增 → 允许Trial"],
                "decisive_reason": "数据与结构证据恢复，允许进入Trial评估。",
                "decision_time_beijing": "2026-09-07T10:25:00+08:00",
            },
        )
        rendered = center.render_canonical_notification(event)
        self.assertEqual(rendered["title"], "【风险许可】禁止新增 → 允许Trial")
        self.assertEqual(rendered["content"].count("### 发生了什么"), 1)
        self.assertIn("### 当前风险许可", rendered["content"])
        self.assertNotIn("### 当前正式状态", rendered["content"])
        self.assertNotIn("错误的producer markdown不应被重复包裹", rendered["content"])
        self.assertIn("### 当前正式动作", rendered["content"])
        self.assertIn("### 下一关注点", rendered["content"])

    def test_all_formal_interrupt_families_are_action_complete(self):
        cases = [
            ("观察机会", "无机会", False),
            ("Trial机会", "观察机会", False),
            ("Confirm机会", "Trial机会", False),
            ("无机会", "观察机会", False),
            ("无机会", "无机会", True),
        ]
        for status, previous, holding_action_changed in cases:
            event = self.event(
                "FORMAL_DECISION_MATERIAL_CHANGE",
                user_action="当前动作已经明确；按正式结论处理。",
                confirmation_context={
                    "security_code": "518880",
                    "security_name": "黄金ETF",
                    "opportunity_status": status,
                    "previous_opportunity_status": previous,
                    "risk_permission": "允许Trial",
                    "previous_risk_permission": "允许Trial",
                    "holding_action_changed": holding_action_changed,
                    "change_summary": ["正式判断发生实质变化"],
                    "decisive_reason": "现有正式证据已改变当前资本或持仓判断。",
                    "current_action": "新增5,000元。" if status in {"Trial机会", "Confirm机会"} else (
                        "卖出1,000份；释放资金保留现金。" if holding_action_changed else "当前不新增。"
                    ),
                    "capital_route": "下一单位资本：现金",
                    "next_validation": "下一正式节点复核失效条件。",
                    "decision_time_beijing": "2026-09-18T14:10:53+08:00",
                },
            )
            rendered = center.render_canonical_notification(event)
            self.assertIsNotNone(rendered)
            for section in (
                "### 发生了什么",
                "### 为什么现在值得关注",
                "### 当前正式动作",
                "### 你需要做什么",
                "### 下一关注点",
                "### 事实时点（北京时间）",
            ):
                self.assertIn(section, rendered["content"])
            self.assertIn("下一单位资本：现金", rendered["content"])
            self.assertNotIn("打开ChatGPT", rendered["content"])

    def test_account_confirmation_remains_specific(self):
        rendered = center.render_canonical_notification(
            self.event(
                "ACCOUNT_FACT_CONFIRMATION",
                content="发现现金变化10,000元尚未解释。",
                user_action="请确认是否发生资金划转，并提供实际金额与时间。",
                confirmation_context={"account_event_time_beijing": "2026-09-18T14:10:53+08:00"},
            )
        )
        self.assertIn("发现现金变化10,000元尚未解释", rendered["content"])
        self.assertIn("提供实际金额与时间", rendered["content"])
        self.assertIn("### 事实时点（北京时间）", rendered["content"])


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
