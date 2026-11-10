from __future__ import annotations

import pytest

from scripts.validate_hyperliquid_consumer_output import (
    HyperliquidConsumerContractError,
    validate_consumer_output,
)


def row():
    return {
        "ashare_code": "159941",
        "decision_effect": "TIMING_FILTER",
        "priority_effect": "保持观察优先级，等待传导",
        "next_trigger": "A股同步传导且本地承接正常时升级；否则维持观察或暂缓新增配置",
    }


def test_consumer_output_requires_concrete_etf_conclusion():
    text = (
        "159941：时机过滤；保持观察优先级，等待传导；"
        "升级条件：A股同步传导且本地承接正常时升级；否则维持观察或暂缓新增配置"
    )
    assert validate_consumer_output(text=text, contributions=[row()])["status"] == "PASS"


def test_consumer_output_rejects_generic_prose():
    with pytest.raises(HyperliquidConsumerContractError):
        validate_consumer_output(
            text="Hyperliquid提供背景，可用于判断全球风险偏好变化。",
            contributions=[row()],
        )


def test_consumer_output_rejects_missing_trigger():
    with pytest.raises(HyperliquidConsumerContractError):
        validate_consumer_output(
            text="159941：时机过滤；保持观察优先级，等待传导",
            contributions=[row()],
        )
