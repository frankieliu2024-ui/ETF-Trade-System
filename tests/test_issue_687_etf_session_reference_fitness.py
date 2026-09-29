from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.formal_etf_opportunity_discovery import attach_formal_quotes
from scripts.query_time_market_refresh import refresh_market_quotes


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "config/market").mkdir(parents=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "config/market/a_share_trading_calendar_2026.json").write_text(json.dumps({
        "coverage_start": "2026-01-01", "coverage_end": "2026-12-31",
        "closed_dates": ["2026-09-25", "2026-09-26", "2026-09-27"],
    }), encoding="utf-8")
    (root / "config/runtime_policy.json").write_text(json.dumps({
        "fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500,
    }), encoding="utf-8")
    return root


def _row(ts_ms: int) -> dict:
    return {
        "name": "测试ETF", "last_price": 1.23, "prev_price": 1.20,
        "open_price": 1.21, "high_price": 1.24, "low_price": 1.20,
        "volume": 1000, "turnover": 1234, "provider_timestamp_ms": ts_ms,
        "provider_symbol": "sz159992",
    }


def test_premarket_etf_gets_latest_legal_completed_session_reference() -> None:
    root = _root()
    now = datetime.fromisoformat("2026-09-29T08:00:00+08:00")
    provider = datetime.fromisoformat("2026-09-28T15:00:00+08:00")
    with patch("scripts.query_time_market_refresh.fetch_tencent_quotes", return_value={
        "159992.SZ": _row(int(provider.timestamp() * 1000))
    }):
        out = refresh_market_quotes(root, ["159992.SZ"], now)
    assert out["failures"] == []
    q = out["quotes"][0]
    assert q["freshness"] == "SESSION_REFERENCE"
    assert q["evidence_fitness"]["observation_evaluation"] is True
    assert q["evidence_fitness"]["executable_price"] is False


def test_premarket_etf_rejects_wrong_session_reference() -> None:
    root = _root()
    now = datetime.fromisoformat("2026-09-29T08:00:00+08:00")
    stale = datetime.fromisoformat("2026-09-24T15:00:00+08:00")
    with patch("scripts.query_time_market_refresh.fetch_tencent_quotes", return_value={
        "159992.SZ": _row(int(stale.timestamp() * 1000))
    }):
        out = refresh_market_quotes(root, ["159992.SZ"], now)
    assert out["quotes"] == []
    assert out["failures"][0]["symbol"] == "159992.SZ"
    assert "latest legal completed-session" in out["failures"][0]["error"]


def test_session_reference_is_ready_for_observation_but_not_execution() -> None:
    formal = {"candidates": [{"code": "159992"}]}
    quote = {
        "symbol": "159992.SZ", "latest_price": 1.23, "quality_status": "PASS",
        "freshness": "SESSION_REFERENCE", "source": "tencent_qq",
    }
    out = attach_formal_quotes(formal, {"quotes": [quote]})
    item = out["candidates"][0]
    assert item["formal_quote_status"] == "SESSION_REFERENCE_READY"
    assert item["formal_quote_evidence_fitness"]["observation_evaluation"] is True
    assert item["formal_quote_evidence_fitness"]["trade_amount_or_shares"] is False
    assert out["formal_quote_coverage"] == 1


def test_unavailable_remains_local_and_does_not_collapse_ready_objects() -> None:
    formal = {"candidates": [{"code": "159992"}, {"code": "515880"}, {"code": "588080"}]}
    quotes = [
        {"symbol": "159992.SZ", "latest_price": 1.23, "quality_status": "PASS", "freshness": "SESSION_REFERENCE"},
        {"symbol": "515880.SH", "latest_price": 1.00, "quality_status": "PASS", "freshness": "FRESH"},
    ]
    out = attach_formal_quotes(formal, {"quotes": quotes})
    assert [x["formal_quote_status"] for x in out["candidates"]] == [
        "SESSION_REFERENCE_READY", "READY", "UNAVAILABLE"
    ]
    assert out["formal_quote_coverage"] == 2
