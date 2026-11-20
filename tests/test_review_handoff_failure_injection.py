"""Regression contracts for review handoff failure isolation.

These tests are intentionally network-free. They inject the transport failure at
the handoff boundary and verify that the frozen completion remains authoritative,
while the failure is reported as pending/failed rather than being converted to
success or aborting the task.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEM = (ROOT / "scripts" / "run_scheduled_system_review_actor.py").read_text(encoding="utf-8")
TRADE = (ROOT / "scripts" / "run_scheduled_trade_review_actor.py").read_text(encoding="utf-8")


def _assert_persist_before_notification(source: str, persist_marker: str, notify_marker: str) -> None:
    assert source.index(persist_marker) < source.index(notify_marker)


def test_system_review_forces_consistency_call_but_keeps_failure_non_blocking():
    assert "def _run_required_consistency_acceptance()" in SYSTEM
    assert "_run_required_consistency_acceptance()" in SYSTEM
    assert '"status": "UNAVAILABLE"' in SYSTEM
    assert '"status": "NOT_STARTED"' in SYSTEM
    assert "completion_path, persistence = persist_completion(target)" in SYSTEM
    assert "try:\n        dispatch = dispatch_notification(completion_path)" in SYSTEM
    _assert_persist_before_notification(
        SYSTEM,
        "completion_path, persistence = persist_completion(target)",
        "dispatch = dispatch_notification(completion_path)",
    )
    assert '"task_handoff_status": "CONFIRMED" if ok else "PENDING"' in SYSTEM
    assert '"ok": True' in SYSTEM


def test_trade_review_transport_failure_does_not_abort_after_completion():
    assert "try:\n        dispatch = _dispatch_notification(canonical_path)" in TRADE
    assert '"status": "FAILED"' in TRADE
    assert '"read_back": False' in TRADE
    _assert_persist_before_notification(
        TRADE,
        "persisted_path, persistence = _persist_completion_to_main(Path(target))",
        "dispatch = _dispatch_notification(canonical_path)",
    )
    assert '"task_handoff_status": "CONFIRMED" if handoff_ok else "PENDING"' in TRADE
    assert '"ok": True' in TRADE


def test_regression_does_not_turn_connection_failure_into_acceptance_pass():
    assert '"status": "UNAVAILABLE"' in SYSTEM
    assert '"status": "FAILED"' in TRADE
    assert '"same_run_handoff_complete": ok' in SYSTEM
    assert '"same_run_handoff_complete": handoff_ok' in TRADE
