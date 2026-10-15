from pathlib import Path


WORKFLOW = Path(".github/workflows/decision-notification.yml")


def test_formal_decision_bds_is_not_a_direct_notification_wake():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Reject direct BDS notification path" in text
    assert "Direct BDS notification intentionally disabled." in text
    assert "formal-content-file" not in text


def test_formal_decision_uses_shared_completion_report_binding():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "DECISION_EVENT_PATH" in text
    assert "REVIEW_EVENT_PATH" in text
    assert "REPORT_HANDOFF_PATH" in text
    assert "python scripts/run_guarded_notification.py center --mode event" in text
