import sys
sys.path.insert(0, "scripts")

from notification_center import render_canonical_notification
from run_guarded_notification import _pulse_event


def test_compact_pulse_contract_bypasses_legacy_wrapper():
    event = {
        "event_type": "PULSE_FACT_UPDATE",
        "presentation_contract": "PULSE_COMPACT_V1",
        "title": "【市场异动】测试",
        "content": "【核心结论】测试正文。\n\n【当前边界】不自动交易。",
        "confirmation_context": {
            "applicable_object": "测试ETF（159999）",
            "before_fact": "机器前事实",
            "after_fact": "机器后事实",
        },
    }
    rendered = render_canonical_notification(event)
    assert rendered["content"] == event["content"]
    assert "变化前后" not in rendered["content"]
    assert rendered["template_family"] == "市场异动"


def test_synthetic_discovery_carries_reason_evidence():
    before = {"candidates": [{"code": "159567", "name": "港股创新药ETF银华"}]}
    after = {
        "formed_at_beijing": "2026-11-07T17:00:00+08:00",
        "candidates": [
            {"code": "159567", "name": "港股创新药ETF银华"},
            {
                "code": "520550",
                "name": "港股红利低波ETF招商",
                "surfaced_states": [
                    {"state": "PERSISTENT_TREND", "evidence": {"return_5": 0.03}}
                ],
                "discovery_spot": {"change_pct": 1.2},
            },
        ],
    }
    event = _pulse_event("discovery_pulse", "Discovery", before, after)
    assert event is not None
    assert "5日+3.0%" in event["content"]
    assert event["presentation_contract"] == "PULSE_COMPACT_V1"
