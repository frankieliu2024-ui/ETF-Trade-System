import pytest

from scripts.business_decision_source import _validate_formal_presentation_layout
from scripts.notification_center import _validate_formal_presentation_layout as validate_notification_layout


def test_formal_primary_marker_requires_whitespace():
    body = "【ETF正式决策】\n\n【业务结论】\n\n1.海外与跨市场"
    with pytest.raises(ValueError, match="primary numbering requires whitespace"):
        _validate_formal_presentation_layout(body)


def test_formal_secondary_marker_requires_whitespace():
    body = "【ETF正式决策】\n\n【业务结论】\n\n1. 一级项目\n\n1）二级项目"
    with pytest.raises(ValueError, match="secondary numbering requires whitespace"):
        _validate_formal_presentation_layout(body)


def test_formal_secondary_item_keeps_hierarchy_after_primary():
    body = "【ETF正式决策】\n\n【业务结论】\n\n1. 一级项目\n\n1） 二级项目"
    _validate_formal_presentation_layout(body)
    assert validate_notification_layout(body) == ""
