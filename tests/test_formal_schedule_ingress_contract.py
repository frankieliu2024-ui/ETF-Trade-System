from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "market-snapshot.yml"


def test_formal_schedule_has_exact_five_beijing_slots():
    text = WORKFLOW.read_text(encoding="utf-8")
    for cron in (
        'cron: "0 1 * * 1-5"',
        'cron: "0 2 * * 1-5"',
        'cron: "0 3 * * 1-5"',
        'cron: "30 5 * * 1-5"',
        'cron: "30 6 * * 1-5"',
    ):
        assert cron in text


def test_formal_schedule_is_market_data_pulse_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Formal Decision consumes current main facts directly" in text
    assert "Create deterministic Formal Decision schedule ingress" not in text
    assert "create_scheduled_formal_request.py" not in text
