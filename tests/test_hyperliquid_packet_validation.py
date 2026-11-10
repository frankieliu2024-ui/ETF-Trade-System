from __future__ import annotations

import pytest

from scripts.validate_hyperliquid_consumer_output import (
    HyperliquidConsumerContractError,
    validate_contribution_packet,
)


def packet():
    return {
        "mapped_objects": [{
            "object_id": "xyz:SMH",
            "observation_as_of": "2026-11-10T03:00:00+08:00",
            "direction_signal": "UP",
            "decision_effect": "TIMING_FILTER",
            "signal_fields": {"pulse_comparison_status": "NOT_COMPARABLE"},
        }],
        "etf_contributions": [{
            "ashare_code": "159941",
            "decision_effect": "TIMING_FILTER",
            "priority_effect": "保持观察优先级，等待传导",
            "next_trigger": "A股同步传导且本地承接正常时升级",
        }],
    }


def test_shared_packet_validation_passes():
    assert validate_contribution_packet(packet())["status"] == "PASS"


def test_shared_packet_validation_rejects_comparable_reason_residue():
    broken = packet()
    broken["mapped_objects"][0]["signal_fields"] = {
        "pulse_comparison_status": "COMPARABLE",
        "comparison_reason": "缺少上一合法脉冲",
    }
    with pytest.raises(HyperliquidConsumerContractError):
        validate_contribution_packet(broken)
