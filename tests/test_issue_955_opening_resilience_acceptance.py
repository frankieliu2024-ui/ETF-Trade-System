from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import refresh_gate
from scripts.formal_etf_opportunity_discovery import attach_formal_quotes
from scripts.query_time_market_refresh import refresh_market_quotes


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "config/market").mkdir(parents=True)
    (root / "config/runtime_policy.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "config/market/a_share_trading_calendar_2026.json").write_text(json.dumps({
        "coverage_start": "2026-01-01", "coverage_end": "2026-12-31", "closed_dates": []
    }), encoding="utf-8")
    (root / "config/runtime_policy.json").write_text(json.dumps({
        "fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500
    }), encoding="utf-8")
    return root


def _row(ts: datetime) -> dict:
    return {
        "name": "测试ETF", "last_price": 1.23, "prev_price": 1.20,
        "open_price": 1.21, "high_price": 1.24, "low_price": 1.20,
        "volume": 1000, "turnover": 1234,
        "provider_timestamp_ms": int(ts.timestamp() * 1000),
        "provider_symbol": "sz159992",
    }


def test_phase_transition_evidence_fitness_matrix():
    root = _root()
    cases = [
        ("2026-09-29T08:00:00+08:00", "2026-09-28T15:00:00+08:00", "SESSION_REFERENCE"),
        ("2026-09-29T09:20:00+08:00", "2026-09-29T09:20:00+08:00", "FRESH"),
        ("2026-09-29T10:00:00+08:00", "2026-09-29T10:00:00+08:00", "FRESH"),
        ("2026-09-29T12:00:00+08:00", "2026-09-29T11:30:00+08:00", "SESSION_REFERENCE"),
        ("2026-09-29T15:30:00+08:00", "2026-09-29T15:00:00+08:00", "SESSION_REFERENCE"),
    ]
    for now_text, provider_text, expected in cases:
        now = datetime.fromisoformat(now_text)
        provider = datetime.fromisoformat(provider_text)
        with patch("scripts.query_time_market_refresh.fetch_tencent_quotes", return_value={
            "159992.SZ": _row(provider)
        }):
            out = refresh_market_quotes(root, ["159992.SZ"], now)
        assert out["failures"] == [], (now_text, out)
        quote = out["quotes"][0]
        assert quote["freshness"] == expected
        if expected == "SESSION_REFERENCE":
            assert quote["evidence_fitness"]["observation_evaluation"] is True
            assert quote["evidence_fitness"]["trade_amount_or_shares"] is False


def test_partial_discovery_failure_is_object_local_and_action_scoped():
    formal = {"candidates": [{"code": "159992"}, {"code": "515880"}, {"code": "588080"}]}
    quotes = [
        {"symbol": "159992.SZ", "latest_price": 1.23, "quality_status": "PASS", "freshness": "SESSION_REFERENCE"},
        {"symbol": "515880.SH", "latest_price": 1.00, "quality_status": "PASS", "freshness": "FRESH"},
    ]
    out = attach_formal_quotes(formal, {"quotes": quotes})
    by_code = {x["code"]: x for x in out["candidates"]}
    assert by_code["159992"]["formal_quote_status"] == "SESSION_REFERENCE_READY"
    assert by_code["159992"]["formal_quote_evidence_fitness"]["observation_evaluation"] is True
    assert by_code["159992"]["formal_quote_evidence_fitness"]["trade_amount_or_shares"] is False
    assert by_code["515880"]["formal_quote_status"] == "READY"
    assert by_code["515880"]["formal_quote_evidence_fitness"]["trade_amount_or_shares"] is True
    assert by_code["588080"]["formal_quote_status"] == "UNAVAILABLE"
    assert out["formal_quote_coverage"] == 2


def test_same_request_identity_beats_newer_historical_directory_entry():
    root = _root()
    request_dir = root / "requests/live_snapshot"
    request_dir.mkdir(parents=True)
    preferred = request_dir / "preferred.json"
    newer = request_dir / "newer.json"
    preferred.write_text(json.dumps({
        "request_id": "formal-current", "requested_at_beijing": "2026-09-29T09:20:00+08:00",
        "query_intent": "FORMAL_INTRADAY_ANALYSIS", "force_refresh": True
    }), encoding="utf-8")
    newer.write_text(json.dumps({
        "request_id": "other-request", "requested_at_beijing": "2026-09-29T09:21:00+08:00",
        "query_intent": "FORMAL_INTRADAY_ANALYSIS", "force_refresh": True
    }), encoding="utf-8")
    with patch.object(refresh_gate, "REQUEST_DIR", request_dir):
        path, req = refresh_gate.latest_wait_request(preferred)
    assert path == preferred
    assert req["request_id"] == "formal-current"


def test_business_reply_readiness_is_not_equated_with_persistence_completion():
    from scripts.business_decision_source import validate_source
    source = {
        "request_type": "BUSINESS_DECISION_SOURCE", "request_id": "r1", "decision_id": "d1",
        "consumed_snapshot": "snap-1", "risk_permission": "PERMITTED", "main_candidate": "NONE",
        "opportunity_status": "无机会", "capital_use": "现金",
        "continued_holding_opportunity_cost": "低", "action_changes_now": "NO",
        "next_change_condition": "risk permission changes", "managed_position_reviews": [],
        "etf_opportunity_reviews": [], "capital_competition": [], "next_unit_capital_use": "现金",
        "decision_evidence_consumption": {
            "request_id": "r1", "layer_1_external_cross_market": "reviewed",
            "layer_2_a_share_internal": "reviewed", "layer_3_etf_opportunity_capital": "reviewed",
            "discovery_to_capital_competition_consumed": True,
            "all_managed_positions_sell_chain_consumed": True,
            "held_etf_additional_capital_consumed": True, "next_unit_capital_use_consumed": True,
        },
    }
    result = validate_source(source, expected_snapshot="snap-1")
    assert result["source_durable"] is True
    assert result["reply_ready"] is True
    assert "formal_completion_validator" not in result
