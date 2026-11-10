from __future__ import annotations

from scripts.hyperliquid_contribution import build_contribution
from scripts.hyperliquid_contribution import _economic_etf_matches


def test_economic_family_mapping_includes_current_discovery_candidate():
    codes = _economic_etf_matches({"name_markers": ["芯片"]})
    assert "588200" in codes


def test_shared_packet_materializes_etf_consumption_results():
    packet = build_contribution(
        context={
            "context_id": "hl-test",
            "latest": {
                "fact_time_beijing": "2026-11-09T10:00:00+08:00",
                "context_id": "hl-test",
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
        },
        task="FORMAL_DECISION",
        node="10:00",
    )
    assert packet["etf_contribution_count"] == 1
    row = packet["etf_contributions"][0]
    assert row["ashare_code"] == "159941"
    assert row["cross_market_relation"]
    assert row["decision_use"]


def test_missing_comparison_has_explicit_reason():
    packet = build_contribution(
        context={"latest": {"observations": [{
            "asset": "xyz:SMH",
            "family": "SEMICONDUCTOR",
            "mapped_assets": ["159941"],
            "mark_px": "90",
            "prev_day_px": "100",
        }]}},
        task="TRADE_REVIEW",
    )
    signal = packet["mapped_objects"][0]["signal_fields"]
    assert signal["pulse_comparison_status"] == "NOT_COMPARABLE"
    assert signal["comparison_reason"]
