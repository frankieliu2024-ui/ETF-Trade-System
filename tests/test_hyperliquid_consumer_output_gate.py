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
        "priority_effect": "方向支持成立但当前不直接追涨；将该ETF列入下一节点优先复核",
        "next_trigger": "下一合法脉冲确认方向延续，并核对A股承接",
    }


def test_consumer_output_requires_concrete_etf_conclusion():
    text = (
        "159941：方向支持成立但当前不直接追涨；将该ETF列入下一节点优先复核；"
        "升级条件：下一合法脉冲确认方向延续，并核对A股承接"
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
