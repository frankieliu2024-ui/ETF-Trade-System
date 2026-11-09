from __future__ import annotations

from scripts.hyperliquid_contribution import build_contribution


def test_four_consumers_share_identical_object_signals():
    context = {
        "context_id": "hl-consistency",
        "latest": {
            "context_id": "hl-consistency",
            "fact_time_beijing": "2026-11-09T10:00:00+08:00",
            "observations": [{
                "asset": "xyz:SMH",
                "family": "SEMICONDUCTOR",
                "mapped_assets": ["159941"],
                "mark_px": "90",
                "prev_day_px": "100",
                "funding": "0.0002",
                "open_interest": "120",
                "premium": "0.003",
                "observation_as_of": "2026-11-09T10:00:00+08:00",
                "pulse_comparison_status": "NOT_COMPARABLE",
                "comparison_reason": "缺少同对象上一合法脉冲",
            }],
        },
    }
    packets = [
        build_contribution(context=context, task=task)
        for task in ("DISCOVERY", "FORMAL_DECISION", "SYSTEM_REVIEW", "TRADE_REVIEW")
    ]
    signatures = [
        (
            packet["mapped_objects"][0]["signal_fields"],
            packet["mapped_objects"][0]["decision_effect"],
            packet["etf_contributions"][0]["cross_market_relation"],
        )
        for packet in packets
    ]
    assert all(item == signatures[0] for item in signatures[1:])


def test_mapping_candidate_is_explicit_when_no_live_signal():
    packet = build_contribution(
        context={
            "mapping": [{"asset": "xyz:SP500", "family": "GLOBAL_RISK_APPETITE"}],
            "latest": {"observations": []},
        },
        task="SYSTEM_REVIEW",
    )
    assert packet["mapping_candidate_count"] == 1
    candidate = packet["mapping_candidates"][0]
    assert candidate["evidence_status"] == "NO_LIVE_SIGNAL"
    assert candidate["decision_use"]
