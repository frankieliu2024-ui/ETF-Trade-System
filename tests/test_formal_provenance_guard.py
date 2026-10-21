import json

import pytest

from scripts.business_decision_source import validate_formal_presentation_provenance


def test_rejects_body_reused_by_another_formal_run(tmp_path):
    live = tmp_path / "requests" / "live_snapshot"
    live.mkdir(parents=True)
    body = "【ETF正式决策】2026-10-21 23:35 正式判断\n\n【业务结论】旧正文"
    (live / "old__business_decision_source.json").write_text(
        json.dumps(
            {
                "request_id": "old__business_decision_source",
                "parent_request_id": "old",
                "decision_id": "old_decision",
                "market_date": "2026-10-21",
                "presentation_content": body,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="already bound"):
        validate_formal_presentation_provenance(
            body,
            parent_request_id="new",
            decision_id="new_decision",
            market_date="2026-10-21",
            root=tmp_path,
        )


def test_allows_idempotent_reuse_within_same_request(tmp_path):
    live = tmp_path / "requests" / "live_snapshot"
    live.mkdir(parents=True)
    body = "【ETF正式决策】2026-10-21 23:35 正式判断\n\n【业务结论】正文"
    (live / "same__business_decision_source.json").write_text(
        json.dumps(
            {
                "request_id": "same__business_decision_source",
                "parent_request_id": "same",
                "decision_id": "same_decision",
                "market_date": "2026-10-21",
                "presentation_content": body,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    digest = validate_formal_presentation_provenance(
        body,
        parent_request_id="same",
        decision_id="same_decision",
        market_date="2026-10-21",
        root=tmp_path,
    )
    assert len(digest) == 64
