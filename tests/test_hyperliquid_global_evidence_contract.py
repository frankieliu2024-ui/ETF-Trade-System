from __future__ import annotations

from datetime import datetime, timezone

from scripts.pulse_history import _parse


def test_comparison_horizon_is_not_assumed_to_be_ten_minutes():
    current = _parse("2026-11-09T10:30:00+08:00")
    previous = _parse("2026-11-09T10:00:00+08:00")
    assert current and previous
    assert int((current - previous).total_seconds()) == 1800


def test_missing_previous_pulse_is_not_comparable():
    row = {
        "pulse_comparison_status": "NOT_COMPARABLE",
        "comparison_reason": "缺少同对象上一合法脉冲",
        "comparison_horizon": "UNKNOWN",
    }
    assert row["pulse_comparison_status"] != "COMPARABLE"
    assert row["comparison_reason"]


def test_relative_delta_never_invents_zero_baseline():
    old_value = 0.0
    current_value = 1.0
    delta_pct = None if old_value == 0 else (current_value - old_value) / abs(old_value)
    assert delta_pct is None
