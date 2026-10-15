import pytest

from scripts.business_decision_source import _validate_formal_presentation_contract


def _source(body: str) -> dict:
    return {
        "presentation_content": body,
        "presentation_binding": {"full_content": body},
    }


def test_formal_presentation_rejects_compressed_report_before_bds_write():
    body = "【ETF正式决策】\n\n" + ("正文\n" * 500)
    with pytest.raises(ValueError, match="6000"):
        _validate_formal_presentation_contract(_source(body))


def test_formal_presentation_rejects_non_identical_presentation_fields():
    body = "【ETF正式决策】\n\n" + ("正文\n" * 2200)
    source = _source(body)
    source["decision_response"] = {"presentation_content": body + "被追加的交接审计"}
    with pytest.raises(ValueError, match="byte-identical"):
        _validate_formal_presentation_contract(source)
