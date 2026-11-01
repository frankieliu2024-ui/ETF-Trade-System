"""Regression: keywords alone cannot satisfy the zero-position comparison."""
from scripts.user_visible_presentation import _explicit_reentry_answer


def test_keyword_stuffing_without_answer_is_rejected():
    item = (
        "纳指ETF（159941）：重新买入；零持仓；迁移成本；失败风险；"
        "右尾；账户功能；因为风险收益需要比较。"
    )
    assert not _explicit_reentry_answer(item)


def test_answer_without_local_reason_is_rejected():
    item = "纳指ETF（159941）：若当前没有该持仓，不会买入。其他段落因为市场变化。"
    assert not _explicit_reentry_answer(item)


def test_explicit_reasoned_negative_counterfactual_is_accepted():
    item = "纳指ETF（159941）：若当前没有该持仓，不会重新买入，因为当前可验证的风险收益不足。"
    assert _explicit_reentry_answer(item)


def test_explicit_reasoned_positive_counterfactual_is_accepted():
    item = "纳指ETF（159941）：如果当前没有该持仓，会考虑买入，因为本节点的趋势与资金承接证据仍成立。"
    assert _explicit_reentry_answer(item)


def test_reason_connector_without_answer_is_rejected():
    item = "纳指ETF（159941）：若当前没有该持仓，需要重新比较现金与机会，因为风险收益尚不确定。"
    assert not _explicit_reentry_answer(item)


def test_reentry_question_and_unrelated_rationale_are_rejected():
    item = "纳指ETF（159941）：如果当前没有该持仓，是否买入？因为需要比较，仍需观察。"
    assert not _explicit_reentry_answer(item)


def test_reasonless_positive_wording_is_rejected():
    item = "纳指ETF（159941）：若当前没有该持仓，会重新买入，因为。"
    assert not _explicit_reentry_answer(item)


def test_existing_willing_to_reallocate_wording_is_accepted():
    item = (
        "纳指ETF（159941）：相较现金，继续占资优于释放，因为风险收益仍可接受。"
        "若当前没有该持仓，按当前价格和证据仍愿意重新配置资本。"
    )
    assert _explicit_reentry_answer(item)


def test_adjacent_reason_without_decision_is_rejected():
    item = (
        "纳指ETF（159941）：因为风险收益尚待核实。"
        "若当前没有该持仓，需要重新比较现金与机会。"
    )
    assert not _explicit_reentry_answer(item)


def test_unrelated_reason_in_another_position_is_rejected():
    item = (
        "纳指ETF（159941）：若当前没有该持仓，不会重新买入。"
        "能源化工ETF：因为趋势强度较高，考虑配置。"
    )
    assert not _explicit_reentry_answer(item)
