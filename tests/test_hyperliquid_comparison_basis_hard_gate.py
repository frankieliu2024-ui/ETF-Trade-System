from __future__ import annotations

from scripts.pulse_history import _parse


def test_comparable_requires_previous_object_time():
    current = _parse("2026-11-09T20:22:33+08:00")
    previous = _parse("")
    assert current is not None
    assert previous is None
    status = "COMPARABLE" if previous is not None else "NOT_COMPARABLE"
    assert status == "NOT_COMPARABLE"


def test_live_legacy_record_cannot_hide_missing_comparison_basis():
    previous = {"asset": "BTC", "observation_as_of": ""}
    current = {"collected_at_beijing": "2026-11-09T20:22:33+08:00"}
    assert not previous.get("observation_as_of")
    assert current["collected_at_beijing"]
