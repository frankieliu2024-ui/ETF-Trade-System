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
