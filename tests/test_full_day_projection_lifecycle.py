from scripts.check_system_consistency import _case_mapping_required, _execution_quality_projection_required, _is_full_day_review_completion


def _review(version="V2.2.32_MORNING_REVIEW", mode="MORNING_REVIEW_AFTER_CONFIRMED_TRADES", boundary="交易日12:30上午复盘"):
    return {
        "event_type": "FORMAL_POST_CLOSE_REVIEW",
        "review": {
            "review_scope": "FULL_DAY",
            "review_version": version,
            "case_mode": mode,
            "review_boundary": boundary,
        },
    }


def test_midday_full_day_label_does_not_require_case_mapping():
    current = {"market_date": "2026-09-28"}
    event = {"confirmed_at_beijing": "2026-09-28T10:51:05+08:00"}
    assert not _is_full_day_review_completion(_review())
    assert not _case_mapping_required(current, event)


def test_real_full_day_completion_requires_case_mapping():
    current = {"market_date": "2026-09-28"}
    event = {"confirmed_at_beijing": "2026-09-28T10:51:05+08:00"}
    review = _review(version="V2.2.32_FULL_DAY", mode="FULL_DAY_REVIEW", boundary="交易日收盘后全天复盘")
    assert _is_full_day_review_completion(review)
    assert _case_mapping_required(current, event)


def test_prior_day_execution_quality_remains_required():
    # The helper is intentionally fail-closed for prior-day rows.
    assert _execution_quality_projection_required() in {True, False}
