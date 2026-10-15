from pathlib import Path


WORKFLOW = Path(".github/workflows/decision-notification.yml")


def test_formal_decision_bds_is_not_a_direct_notification_wake():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "requests/live_snapshot/*__formal_decision_completion.json" in text
    assert "events/reviews/formal_*.json" in text
    assert "formal-content-file" not in text


def test_formal_decision_uses_shared_completion_report_binding():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "DECISION_EVENT_PATH" not in text
    assert "REVIEW_EVENT_PATH" in text
    assert "REPORT_HANDOFF_PATH" in text
    assert "python scripts/run_guarded_notification.py center --mode event" in text


def test_market_snapshot_cannot_project_formal_completion():
    snapshot = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    assert "Formal Decision completion push is rejected on market-snapshot" in snapshot
    assert "request_files=\"$completion_file\"" not in snapshot
    assert "process_state_sync_request.py \"$request_files\"" in snapshot
