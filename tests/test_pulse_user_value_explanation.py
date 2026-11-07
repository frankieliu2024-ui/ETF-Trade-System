from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "run_guarded_notification.py"


def test_pulse_body_has_object_specific_value_explanation():
    text = SOURCE.read_text(encoding="utf-8")
    assert "_pulse_object_impact" in text
    assert "下一次正式判断要先核对这些ETF自身是否同步" in text


def test_hyperliquid_signal_fields_are_translated_for_users():
    text = SOURCE.read_text(encoding="utf-8")
    assert "资金费率反映多空持仓的周期性资金成本" in text
    assert "未平仓量反映市场中尚未平仓的合约规模" in text
    assert "溢价反映合约价格相对参考现货的偏离" in text
