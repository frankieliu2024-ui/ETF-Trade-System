from pathlib import Path


def test_formal_request_ingress_has_one_canonical_path():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
    actor = (root / "scripts" / "create_scheduled_formal_request.py").read_text(encoding="utf-8")
    assert '"data/requests/**"' in workflow
    assert "Canonicalize legacy formal request ingress" in workflow
    assert 'REQUEST_DIR = ROOT / "requests" / "live_snapshot"' in actor
    assert "formal_context_recovery_request" in actor

def test_auxiliary_query_cannot_replace_canonical_context_on_empty_read():
    root = Path(__file__).resolve().parents[1]
    query = (root / "scripts" / "query_market_object.py").read_text(encoding="utf-8")
    assert 'tmp = path.with_suffix(path.suffix + ".tmp")' in query
    assert 'if raw.strip()' in query
    assert 'else:\n                return' in query
