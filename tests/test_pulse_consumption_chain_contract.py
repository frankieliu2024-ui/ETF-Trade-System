from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_pulse_builders_are_deleted_and_guarded_path_is_canonical():
    text = (ROOT / "scripts/notification_center.py").read_text(encoding="utf-8")
    assert "def _discovery_pulse_event" not in text
    assert "def _hyperliquid_pulse_event" not in text
    assert "def pulse_formal_decision_pending_event" not in text
    guarded = (ROOT / "scripts/run_guarded_notification.py").read_text(encoding="utf-8")
    assert "def _pulse_candidates" in guarded
    assert "def _integrated_pulse_event" in guarded
    assert "def _pulse_candidates_from_payloads" in guarded
    assert "def _pulse_event" not in guarded


def test_us_scheduled_pulse_binds_workflow_run_identity():
    text = (ROOT / ".github/workflows/us-extended-hours-pulse.yml").read_text(encoding="utf-8")
    assert 'occurrence_id=os.environ.get("GITHUB_RUN_ID", "")' in text


def test_hyperliquid_history_is_validated_after_update():
    text = (ROOT / "scripts/build_overseas_context.py").read_text(encoding="utf-8")
    assert "Hyperliquid pulse history was not updated" in text
    assert "Hyperliquid pulse history has no latest identity after update" in text


def test_hyperliquid_is_regenerated_for_each_eligible_overseas_pulse():
    builder = (ROOT / "scripts/build_overseas_context.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/overseas-preopen-pulse.yml").read_text(encoding="utf-8")
    assert '"regeneration_required"] = True' in builder
    assert '"generation_identity"]' in builder
    assert "Verify Hyperliquid node was regenerated for this pulse" in workflow
    assert "current == previous" in workflow
