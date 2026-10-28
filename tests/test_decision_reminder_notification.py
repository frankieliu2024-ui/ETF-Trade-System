from scripts.notification_center import canonical_template_family, render_canonical_notification


def test_formal_decision_pending_uses_decision_reminder_template():
    event = {
        "event_type": "FORMAL_DECISION_PENDING",
        "type": "决策提醒",
        "title": "【决策提醒】建议运行【ETF正式决策】",
        "content": "海外市场波动扩大，持仓风险状态需要复核。",
        "source": "decision_trigger",
        "user_action": "建议立即运行【ETF正式决策】。",
        "confirmation_context": {
            "evidence_time_beijing": "2026-10-28T10:15:00+08:00",
        },
    }
    assert canonical_template_family(event) == "决策提醒"
    rendered = render_canonical_notification(event)
    assert rendered["title"] == "【决策提醒】建议运行【ETF正式决策】"
    assert "不是正式决策正文" in rendered["content"]
    assert "不会自动下单或调仓" in rendered["content"]
