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
