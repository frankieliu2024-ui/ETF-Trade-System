from pathlib import Path


def test_formal_decision_does_not_create_market_fact_ingress():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
    assert "Create deterministic Formal Decision schedule ingress" not in workflow
    assert "create_scheduled_formal_request.py" not in workflow
    assert "Formal Decision consumes current main facts directly" in workflow


def test_auxiliary_query_cannot_replace_canonical_context_on_empty_read():
    root = Path(__file__).resolve().parents[1]
    query = (root / "scripts" / "query_market_object.py").read_text(encoding="utf-8")
    assert 'tmp = path.with_suffix(path.suffix + ".tmp")' in query
    assert 'if raw.strip()' in query
    assert 'else:\n                return' in query
