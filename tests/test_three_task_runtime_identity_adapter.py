from scripts.task_runtime_identity import derive_runtime_identity, derive_request_identity, derive_scheduled_review_identity


def test_manual_identity_never_uses_scheduled_prefix():
    run_id, occurrence_id = derive_runtime_identity(
        "ETF交易复盘", "review-1", "2026-10-22T12:30:00+08:00",
        "MANUAL_SCHEDULED_TASK"
    )
    assert run_id.startswith("manual_")
    assert occurrence_id.startswith("manual_")


def test_automatic_identity_uses_scheduled_prefix():
    run_id, occurrence_id = derive_runtime_identity(
        "ETF正式决策", "formal-1", "2026-10-22T13:30:00+08:00",
        "AUTOMATIC_SCHEDULED_TASK"
    )
    assert run_id.startswith("scheduled_")
    assert occurrence_id.startswith("scheduled_")


def test_supplied_identity_is_preserved():
    assert derive_runtime_identity(
        "ETF系统复核", "system-1", "2026-10-22T19:30:00+08:00",
        "AUTOMATIC_SCHEDULED_TASK", "scheduled-run", "scheduled-occurrence"
    ) == ("scheduled-run", "scheduled-occurrence")


def test_missing_request_identity_is_derived_without_history():
    request_id = derive_request_identity(
        "ETF系统复核", "2026-10-27T10:30:00+08:00", "MANUAL_SCHEDULED_TASK"
    )
    assert request_id.startswith("manual-system-review-20261027103000-")


def test_shared_scheduled_review_identity_is_consistent():
    request_id, run_id, occurrence_id = derive_scheduled_review_identity(
        "ETF系统复核", "2026-10-27T10:30:00+08:00", "MANUAL_SCHEDULED_TASK"
    )
    assert request_id
    assert run_id.startswith("manual_")
    assert occurrence_id == run_id
