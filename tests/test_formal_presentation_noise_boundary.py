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


def test_public_formal_presentation_uses_bracketed_headings_and_plain_single_items():
    content = (
        "风险许可及其边界\n\n"
        "1. 当前以防守为主。\n\n"
        "数据时点与事实性质\n\n"
        "1. 正式收盘参考。\n\n"
        "交接状态：PROJECTION_INCOMPLETE。"
    )
    normalized = _normalize_formal_presentation_layout(content)
    assert "【风险许可及其边界】" in normalized
    assert "【数据时点与事实性质】" in normalized
    assert "当前以防守为主。" in normalized
    assert "1. 当前以防守为主。" not in normalized
    assert "交接状态" not in normalized


def test_machine_terms_are_translated_before_freeze():
    content = (
        "全市场候选扫描\n\n"
        "1. 全市场Discovery状态READY、覆盖COMPLETE。\n\n"
        "2. 日内路径由13个离散快照组成，覆盖MEDIUM。\n\n"
        "3. HOLD/追加/REDUCE/EXIT比较。"
    )
    normalized = _normalize_formal_presentation_layout(content)
    assert "【全市场机会发现】" in normalized
    assert "Discovery状态READY" not in normalized
    assert "覆盖COMPLETE" not in normalized
    assert "MEDIUM" not in normalized
    assert "HOLD/追加/REDUCE/EXIT比较" not in normalized
