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
