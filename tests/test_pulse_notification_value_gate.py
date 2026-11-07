from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "run_guarded_notification.py"


def test_discovery_pulse_ignores_metadata_only_changes():
    text = SOURCE.read_text(encoding="utf-8")
    assert "_discovery_candidates_changed" in text
    assert 'source == "discovery_pulse" and not _discovery_candidates_changed' in text


def test_hyperliquid_pulse_requires_decision_bearing_fields():
    text = SOURCE.read_text(encoding="utf-8")
    assert "_hyperliquid_decision_signal_changed" in text
    assert '("funding", "open_interest", "premium")' in text
    assert 'if not _hyperliquid_decision_signal_changed(before, after):' in text


def test_pulse_contract_keeps_machine_evidence_separate_from_user_notification():
    text = SOURCE.read_text(encoding="utf-8")
    assert "Price snapshots remain machine evidence" in text
    assert "funding, open interest and premium" in text


def test_hyperliquid_machine_jitter_is_not_user_notification():
    from unittest.mock import patch
    import sys
    sys.path.insert(0, "scripts")
    from run_guarded_notification import _pulse_event
    before = {"observations": [{"asset": "xyz:NVDA", "funding": "0.00001", "open_interest": "10000", "premium": "0.0001"}]}
    after = {"observations": [{"asset": "xyz:NVDA", "funding": "0.00002", "open_interest": "10020", "premium": "0.0002"}]}
    with patch("run_guarded_notification._hyperliquid_relevant_asset_objects", return_value={"xyz:NVDA": ["科创50ETF（588000）"]}):
        assert _pulse_event("hyperliquid_pulse", "Hyperliquid", before, after) is None


def test_discovery_without_candidate_evidence_is_not_user_notification():
    import sys
    sys.path.insert(0, "scripts")
    from run_guarded_notification import _pulse_event
    before = {"candidates": [{"code": "159567", "name": "创新药ETF"}]}
    after = {"candidates": [{"code": "159567", "name": "创新药ETF"}, {"code": "520550", "name": "红利低波ETF"}]}
    assert _pulse_event("discovery_pulse", "Discovery", before, after) is None
