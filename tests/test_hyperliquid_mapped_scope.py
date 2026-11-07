import sys
from unittest.mock import patch

sys.path.insert(0, "scripts")
from run_guarded_notification import _integrated_pulse_event


def test_unmapped_asset_does_not_create_user_event():
    before = {"observations": [{"asset": "BTC", "funding": "0.1"}]}
    after = {"formed_at_beijing": "2026-11-07T15:00:00+08:00", "observations": [{"asset": "BTC", "funding": "0.2"}]}
    with patch("run_guarded_notification._hyperliquid_relevant_asset_objects", return_value={}):
        assert _integrated_pulse_event(
            {"candidates": [{"code": "159992", "name": "创新药ETF"}]},
            {"formed_at_beijing": "2026-11-07T15:00:00+08:00", "candidates": [{"code": "159992", "name": "创新药ETF", "discovery_spot": {"change_pct": 1.2}}]},
            before,
            after,
        ) is None


def test_user_event_requires_same_etf_evidence_from_both_engines():
    before = {"observations": [{"asset": "xyz:XBI", "funding": "0.010%"}]}
    after = {"formed_at_beijing": "2026-11-07T15:00:00+08:00", "observations": [{"asset": "xyz:XBI", "funding": "0.030%"}]}
    with patch("run_guarded_notification._hyperliquid_relevant_asset_objects", return_value={"xyz:XBI": ["创新药ETF（159992）"]}):
        event = _integrated_pulse_event(
            {"candidates": []},
            {"formed_at_beijing": "2026-11-07T15:00:00+08:00", "candidates": [{"code": "159992", "name": "创新药ETF", "discovery_spot": {"change_pct": 1.2}}]},
            before,
            after,
        )
    assert event is not None
    assert "创新药ETF（159992）" in event["content"]
    assert event["confirmation_context"]["affected_etfs"] == ["创新药ETF（159992）"]
