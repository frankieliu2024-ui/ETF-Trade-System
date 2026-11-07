from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_notification_center_does_not_produce_legacy_pulse_events():
    text = (ROOT / "scripts/notification_center.py").read_text(encoding="utf-8")
    builder_area = text.split("def choose_event", 1)[1].split("LIFECYCLE_STATUSES", 1)[0]
    assert "pulse_formal_decision_pending_event" not in builder_area


def test_us_scheduled_pulse_binds_workflow_run_identity():
    text = (ROOT / ".github/workflows/us-extended-hours-pulse.yml").read_text(encoding="utf-8")
    assert 'occurrence_id=os.environ.get("GITHUB_RUN_ID", "")' in text


def test_hyperliquid_history_is_validated_after_update():
    text = (ROOT / "scripts/build_overseas_context.py").read_text(encoding="utf-8")
    assert "Hyperliquid pulse history was not updated" in text
    assert "Hyperliquid pulse history has no latest identity after update" in text
