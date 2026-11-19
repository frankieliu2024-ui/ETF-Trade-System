from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "market-snapshot.yml"


def test_market_snapshot_uses_unified_fixed_pulse_not_formal_task_slots():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'cron: "0,10,20,30,40,50 0-8 * * 1-5"' in text
    assert 'cron: "0 1 * * 1-5"' not in text
    assert 'cron: "30 5 * * 1-5"' not in text


def test_formal_schedule_is_market_data_pulse_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Formal Decision consumes current main facts directly" in text
    assert "Create deterministic Formal Decision schedule ingress" not in text
    assert "create_scheduled_formal_request.py" not in text
