import json

import scripts.notification_center as notification_center
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
            "trigger_reason": "风险边界发生变化",
            "market_snapshot": "2026-10-27交易日，最近合法节点为close，行情事实时点为2026-10-27T15:07:52+08:00，状态为READY。",
            "overseas_snapshot": "海外与亚洲上下文质量为数据可用，生成时点为2026-10-28T08:40:34+08:00。",
            "account_snapshot": "总资产171,078.77元，现金115,294.47元，当日盈亏-578.90元；账户事实时点为2026-10-27T15:36:44+08:00。",
            "positions": ["纳指ETF（159941）12200份，持仓盈亏+2.16%"],
            "risk_snapshot": "当前风险复核区间为RISK_CONTROL，增强风险复核=是；当前资本比较主候选为尚未形成。",
        },
    }
    assert canonical_template_family(event) == "决策提醒"
    rendered = render_canonical_notification(event)
    assert rendered["title"] == "【决策提醒】建议运行【ETF正式决策】"
    assert "当前事实" in rendered["content"]
    assert "实际持仓" in rendered["content"]
    assert "为什么需要重新判断" in rendered["content"]
    assert "不是正式决策正文" in rendered["content"]
    assert "不会自动下单或调仓" in rendered["content"]


def test_formal_decision_pending_reads_current_facts(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "decision_trigger.json").write_text(json.dumps({
        "status": "TRIGGERED",
        "requires_formal_reassessment": True,
        "trigger_type": "RISK_BOUNDARY_CROSSED",
        "idempotency_key": "risk-1",
        "evidence_change": "风险区间由常态进入控制区间",
        "evidence_time": "2026-10-28T10:15:00+08:00",
        "e2e_status": "READY",
    }), encoding="utf-8")
    (state / "CURRENT.json").write_text(json.dumps({
        "market_date": "2026-10-28", "latest_valid_node": "live",
        "captured_at": "2026-10-28T10:15:00+08:00", "node_status": "READY",
    }), encoding="utf-8")
    (state / "account_fact.json").write_text(json.dumps({
        "total_asset": 100000, "cash": 50000, "daily_pnl": -100,
        "updated_at": "2026-10-28T10:14:00+08:00",
        "positions": [{"name": "纳指ETF", "code": "159941", "quantity": 1000, "holding_pnl_pct": 1.2}],
    }), encoding="utf-8")
    (state / "decision_context.json").write_text(json.dumps({
        "decision_trigger": {"risk_zone": "RISK_CONTROL", "enhanced_risk_review_required": True},
        "capital_efficiency_ranking": {"top_candidate": None},
    }), encoding="utf-8")
    (state / "overseas_context.json").write_text(json.dumps({
        "quality_status_cn": "数据可用", "generated_at_beijing": "2026-10-28T10:14:00+08:00",
    }), encoding="utf-8")
    previous = notification_center.STATE
    notification_center.STATE = state
    try:
        event = notification_center.formal_decision_pending_event()
    finally:
        notification_center.STATE = previous
    assert event["event_type"] == "FORMAL_DECISION_PENDING"
    context = event["confirmation_context"]
    assert "风险区间由常态进入控制区间" in context["evidence_change"]
    assert "纳指ETF（159941） 1000份" in context["positions"]
    assert "总资产100,000.00元" in context["account_snapshot"]
