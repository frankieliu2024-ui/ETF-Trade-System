from scripts.business_decision_source import _normalize_formal_presentation_layout


def test_handoff_diagnostics_are_not_part_of_frozen_public_content():
    content = (
        "【ETF正式决策】2026-10-21 23:35 正式判断\n\n"
        "【业务结论】\n\n完整业务正文。\n\n"
        "【最可能出错的地方】\n\n正文风险。\n\n"
        "交接状态：PROJECTION_INCOMPLETE；BDS未回读。\n\n"
        "置信度：中"
    )
    normalized = _normalize_formal_presentation_layout(content)
    assert "交接状态" not in normalized
    assert "PROJECTION_INCOMPLETE" not in normalized
    assert "BDS未回读" not in normalized
    assert normalized.endswith("【最可能出错的地方】\n\n正文风险。")


def test_business_body_without_diagnostics_is_unchanged():
    content = "【ETF正式决策】2026-10-21 23:35 正式判断\n\n【业务结论】\n\n正文。"
    assert _normalize_formal_presentation_layout(content) == content
