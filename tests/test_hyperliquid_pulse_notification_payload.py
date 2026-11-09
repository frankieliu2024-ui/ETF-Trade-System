import sys

sys.path.insert(0, "scripts")
from run_guarded_notification import _pulse_test_candidates


def test_synthetic_pulse_exercises_one_integrated_event():
    events = _pulse_test_candidates()
    assert len(events) == 1
    event = events[0]
    assert event["source"] == "dual_engine_pulse"
    assert "创新药ETF银华（159992）" in event["title"]
    assert "【Discovery依据】" in event["content"]
    assert "【Hyperliquid依据】" not in event["content"]
    assert "Hyperliquid具体依据：" in event["content"]
    assert "【对ETF系统的影响】" in event["content"]
    assert "Hyperliquid可见采集边界：" in event["content"]
    assert event["confirmation_context"]["hyperliquid_observation_time_beijing"]
    assert "5日+2.5%" in event["content"]
    assert "资金费率" not in event["content"]
    assert "未平仓量" not in event["content"]
    assert "溢价" not in event["content"]
    assert event["confirmation_context"]["affected_etfs"] == ["创新药ETF银华（159992）"]
