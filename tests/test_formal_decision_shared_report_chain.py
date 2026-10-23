from pathlib import Path


WORKFLOW = Path(".github/workflows/decision-notification.yml")


def test_formal_decision_bds_is_not_a_direct_notification_wake():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "requests/live_snapshot/*.json" in text
    assert "events/reviews/formal_*.json" in text
    assert "formal-content-file" not in text


def test_formal_decision_uses_shared_completion_report_binding():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "DECISION_EVENT_PATH" not in text
    assert "REVIEW_EVENT_PATH" in text
    assert "REPORT_HANDOFF_PATH" in text
    assert "python scripts/run_guarded_notification.py center --mode event" in text


def test_market_snapshot_projects_formal_completion_through_existing_owner():
    snapshot = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    assert "Project Formal Decision completion through market snapshot owner" in snapshot
    assert "request_files=\"$completion_file\"" in snapshot
    assert "python scripts/process_state_sync_request.py \"$TRIGGERING_REQUEST_FILE\"" in snapshot
    assert "PushPlus" not in snapshot

def test_market_snapshot_builds_completion_from_durable_formal_bds():
    snapshot = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    assert "Build Formal Decision completion from durable business source" in snapshot
    assert "--from-bds \\\"$BUSINESS_SOURCE_FILE\\\"" in snapshot
    assert 'completion_id="${parent_request_id}__scheduled_review_completion"' in snapshot
    assert "git add events/reviews requests/live_snapshot" in snapshot
