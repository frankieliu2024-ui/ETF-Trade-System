from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "market-snapshot.yml"


def test_market_snapshot_has_no_formal_context_steps(self):
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Build state context and candidates" not in text
    assert "Build E2E usability state" not in text
    assert "Build post-market review context" not in text

