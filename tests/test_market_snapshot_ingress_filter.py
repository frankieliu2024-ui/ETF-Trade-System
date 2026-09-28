from pathlib import Path


def test_downstream_decision_artifacts_are_not_snapshot_ingress():
    workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    assert "__business_decision_source" in workflow
    assert "__formal_completion" in workflow
    assert "grep -vE" in workflow
