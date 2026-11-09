from scripts.hyperliquid_user_explanation import explain_for_notification


def test_notification_does_not_rebuild_mapping_from_family():
    text = explain_for_notification(text="SOX费城半导体指数出现极端波动", target="SOX")
    assert "没有当前对象的固定ETF绑定" in text or "SOX" in text


def test_notification_contract_uses_contribution_effect_labels():
    source = open("scripts/hyperliquid_user_explanation.py", encoding="utf-8").read()
    assert "etf_bindings" in source
    assert "decision_effect" in source
    assert "CROWDING_RISK" in source
    assert "10分钟变化" in source
