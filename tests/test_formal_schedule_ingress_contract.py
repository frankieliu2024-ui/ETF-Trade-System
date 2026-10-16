from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "market-snapshot.yml"
SCRIPT = Path(__file__).parents[1] / "scripts" / "create_scheduled_formal_request.py"


def test_formal_schedule_has_exact_four_beijing_slots():
    text = WORKFLOW.read_text(encoding="utf-8")
    for cron in (
        'cron: "0 2 * * 1-5"',
        'cron: "0 3 * * 1-5"',
        'cron: "30 5 * * 1-5"',
        'cron: "30 6 * * 1-5"',
    ):
        assert cron in text


def test_formal_schedule_ingress_is_idempotent_and_canonical():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "occurrence_id" in text
    assert "CHATGPT_SCHEDULED_FORMAL_ANALYSIS" in text
    assert "duplicate_equivalent_refresh_forbidden" in text
    assert "GITHUB_SCHEDULED_FORMAL_DECISION" in text


def test_automatic_identity_is_runtime_bound_and_fail_closed():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'os.environ.get("TASK_ID", "").strip()' in text
    assert 'os.environ.get("TASK_RUN_ID", "").strip()' in text
    assert 'os.environ.get("OCCURRENCE_ID", "").strip()' in text
    assert 'MISSING_AUTOMATIC_RUNTIME_IDENTITY' in text
    assert 'startswith("scheduled_")' in text
    assert '"query_intent": "FORMAL_INTRADAY_ANALYSIS"' in text


def test_manual_entry_is_not_created_by_automatic_ingress_script():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'ENTRY_MODE' in text
    assert 'MANUAL_SCHEDULED_TASK' in text
    assert 'AUTOMATIC_SCHEDULED_TASK' in text
