"""Guard against a fabricated successful System Review when no same-run evidence exists."""
from pathlib import Path


def test_system_review_actor_requires_explicit_review_evidence():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_scheduled_system_review_actor.py").read_text(encoding="utf-8")
    assert 'if not args.review_json:' in source
    assert 'default PASS is prohibited' in source
    assert 'review.get("task_id") != "ETF系统复核"' in source
    assert 'review.get("market_date") != args.market_date' in source
    assert 'review = {"status": "PASS"' not in source
