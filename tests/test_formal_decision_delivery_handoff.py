from pathlib import Path


WORKFLOW = Path(".github/workflows/decision-notification.yml")


def test_formal_delivery_accepts_explicit_repository_dispatch():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "repository_dispatch:" in text
    assert "formal_decision_bds_written" in text
    assert "github.event.client_payload.formal_content_path" in text
    assert "github.event.client_payload.formal_content_hash" in text


def test_formal_delivery_keeps_explicit_identity_binding():
    text = WORKFLOW.read_text(encoding="utf-8")
    for field in (
        "formal_request_id",
        "formal_parent_request_id",
        "formal_entry_mode",
        "formal_source",
        "formal_bds_commit",
        "formal_content_hash",
    ):
        assert f"github.event.client_payload.{field}" in text
