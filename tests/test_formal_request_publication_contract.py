from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "market-snapshot.yml"


def test_formal_context_steps_require_successful_query_context():
    text = WORKFLOW.read_text(encoding="utf-8")
    state_block = text.split("      - name: Build state context and candidates", 1)[1].split(
        "      - name: Build E2E usability state", 1
    )[0]
    e2e_block = text.split("      - name: Build E2E usability state", 1)[1].split(
        "      - name: Build post-market review context", 1
    )[0]
    assert "steps.query_context.outcome == 'success'" in state_block
    assert "steps.query_context.outcome == 'success'" in e2e_block


def test_final_validation_uses_triggering_request_identity():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'TRIGGERING_REQUEST_ID' in text
    assert 'triggering_path = Path(os.environ.get("TRIGGERING_REQUEST_FILE") or "")' in text


def test_failed_formal_context_does_not_publish_stale_derived_state():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'steps.query_context.outcome' in text
    assert 'data/state/e2e_status.json' in text
    assert 'Never publish stale request-bound projections' in text
