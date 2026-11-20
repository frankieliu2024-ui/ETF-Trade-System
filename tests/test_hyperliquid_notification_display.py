from scripts.hyperliquid_user_explanation import _delta_explanation, _direction_effect_text, explain_for_notification


def test_notification_does_not_rebuild_mapping_from_family():
    text = explain_for_notification(text="SOX费城半导体指数出现极端波动", target="SOX")
    assert "没有当前对象的固定ETF绑定" in text or "SOX" in text


def test_notification_contract_uses_contribution_effect_labels():
    source = open("scripts/hyperliquid_user_explanation.py", encoding="utf-8").read()
    assert "etf_bindings" in source
    assert "decision_effect" in source
    assert "CROWDING_RISK" in source
    assert "10分钟变化" in source


def test_notification_uses_central_alias_registry_for_n225():
    text = explain_for_notification(text="日经225指数（N225）出现极端波动", target="N225")
    assert "等待下一次贡献包刷新" in text or "EWJ" in text


def test_notification_resolves_a_share_index_family_mapping():
    text = explain_for_notification(text="创业板指（399006）出现极端波动", target="创业板指（399006）")
    assert "XYZ100" in text
    assert "159781" in text or "科创创业ETF" in text
    assert "结论为" in text


def test_down_direction_does_not_use_upward_chasing_language():
    assert _direction_effect_text("DOWN", "TIMING_FILTER") == "方向偏弱，但当前不直接追随下跌，等待下一合法脉冲确认"


def test_open_interest_explanation_does_not_claim_long_or_short_direction():
    text = _delta_explanation({"funding": 0.0, "open_interest": 43.9802, "premium": -0.0002}, "DOWN")
    assert "未结束的市场押注增加" in text
    assert "不能判断新增的是多头还是空头" in text
    assert "价格一定" not in text


def test_open_interest_decrease_is_explained_as_position_exit():
    text = _delta_explanation({"funding": 0.0, "open_interest": -31519.7580, "premium": -0.0005}, "DOWN")
    assert "未结束押注退出" in text
    assert "不能判断是多头退出还是空头平仓" in text
