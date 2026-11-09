from scripts.hyperliquid_contribution import build_contribution


def test_preopen_contribution():
    result = build_contribution(
        context={"generated_at_beijing": "2026-11-08T07:55:00+08:00", "observations": [{"asset": "xyz:CXMT", "family": "CHINA_MEMORY_CHAIN", "direction": "negative"}]},
        task="FORMAL_DECISION",
        node="09:00",
    )
    assert result["status"] == "READY"
    assert result["phase"] == "PREOPEN_OVERNIGHT_AGGREGATION"
    assert result["mapped_object_count"] == 1
    assert "preopen" in result["node_roles"]


def test_intraday_and_post_close_roles():
    intraday = build_contribution(
        context={"generated_at_beijing": "2026-11-08T10:00:00+08:00", "observations": [{"asset": "xyz:SMH", "family": "SEMICONDUCTOR"}]},
        task="FORMAL_DECISION",
        node="10:00",
    )
    post_close = build_contribution(
        context={"generated_at_beijing": "2026-11-08T16:05:00+08:00", "observations": [{"asset": "xyz:GOLD", "family": "GOLD"}]},
        task="TRADE_REVIEW",
        node="20:30",
    )
    assert intraday["phase"] == "A_SHARE_ACTIVE_WINDOW"
    assert post_close["phase"] == "POST_WINDOW_REFERENCE"
    assert post_close["task"] == "TRADE_REVIEW"


def test_mapping_catalog_is_not_collapsed_into_no_mapping():
    result = build_contribution(
        context={"generated_at_beijing": "2026-11-09T10:59:59+08:00", "observations": [], "mapping": [
            {"asset": "xyz:SMH", "family": "SEMICONDUCTOR", "etf_codes": ["159992"]},
            {"asset": "xyz:CXMT", "family": "CHINA_MEMORY_CHAIN"},
        ]},
        task="FORMAL_DECISION",
        node="11:00",
    )
    assert result["status"] == "DEGRADED"
    assert result["mapped_object_count"] == 0
    assert result["mapping_candidate_count"] == 2
    assert result["mapping_coverage_status"] == "CONFIGURED_CANDIDATES_NO_LIVE_SIGNAL"
    assert result["mapping_candidates"][0]["decision_effect"] == "INSUFFICIENT"
    assert result["mapping_candidates"][0]["signal_fields"] == {}


def test_industry_mapping_resolves_to_current_etf_objects():
    result = build_contribution(
        context={
            "generated_at_beijing": "2026-11-09T10:59:59+08:00",
            "mapping": [{
                "asset": "xyz:SMH",
                "family": "SEMICONDUCTOR",
                "name_markers": ["通信", "设备"],
            }],
            "observations": [{"asset": "xyz:SMH", "direction": "positive"}],
        },
        task="FORMAL_DECISION",
        node="11:00",
    )
    assert result["status"] == "READY"
    assert result["mapped_object_count"] == 1
    assert result["mapped_objects"][0]["mapping_reason"] == "ECONOMIC_FAMILY_NAME_MARKER_MAPPING"
    assert result["mapped_objects"][0]["mapped_assets"]


def test_industry_mapping_includes_discovery_candidates():
    result = build_contribution(
        context={
            "generated_at_beijing": "2026-11-09T11:00:00+08:00",
            "mapping": [{
                "asset": "xyz:BABA",
                "family": "CHINA_TECH_RISK",
                "name_markers": ["港股", "互联网", "科技"],
            }],
            "observations": [{"asset": "xyz:BABA", "direction": "positive"}],
        },
        task="DISCOVERY",
    )
    assert result["mapped_object_count"] == 1
    assert result["mapped_objects"][0]["mapped_assets"]


def test_missing_direction_is_derived_from_price_boundary():
    result = build_contribution(
        context={
            "latest": {
                "fact_time_beijing": "2026-11-09T16:22:39+08:00",
                "observations": [{
                    "asset": "xyz:SMH",
                    "family": "SEMICONDUCTOR",
                    "mark_px": "90",
                    "prev_day_px": "100",
                    "collected_at_beijing": "2026-11-09T16:20:00+08:00",
                }],
            },
        },
        task="FORMAL_DECISION",
        node="14:30",
    )
    item = result["mapped_objects"][0]
    assert item["signal_fields"]["direction"] == "DOWN"
    assert item["signal_fields"]["direction_source"] == "MARK_VS_PREV_DAY"
    assert item["observation_as_of"] == "2026-11-09T16:20:00+08:00"
    assert item["decision_effect"] == "TIMING_FILTER"
