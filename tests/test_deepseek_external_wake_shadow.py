import json

import pytest

from scripts.deepseek_external_wake_shadow import classify_snapshot, normalize_snapshot, validate_wake_output


def valid_snapshot():
    return {
        "observed_at": "2026-09-11T21:30:00+08:00",
        "session": "US_REGULAR",
        "market_family": "US",
        "proxies": {
            "NDX": {"change_pct": 1.1, "session": "REGULAR", "freshness_status": "FRESH"},
            "SOX": {"change_pct": 1.88, "session": "REGULAR", "freshness_status": "FRESH"},
        },
        "previous_snapshot_hash": "opaque",
    }


def test_snapshot_allowlist_excludes_business_fields():
    raw = valid_snapshot()
    raw["trade_action"] = "BUY"
    with pytest.raises(ValueError):
        normalize_snapshot(raw)


def test_missing_secret_fails_open_without_marker():
    result = classify_snapshot(valid_snapshot(), "")
    assert result["status"] == "NO_WAKE"
    assert result["marker"] is None


def test_wake_output_contract_is_exact_and_business_free():
    result = validate_wake_output({
        "wake": True,
        "wake_class": "MARKET_STATE_CHANGE",
        "confidence": "high",
        "reason_class": "SESSION_TRANSITION",
    })
    assert result["wake"] is True
    with pytest.raises(ValueError):
        validate_wake_output({
            "wake": True,
            "wake_class": "MARKET_STATE_CHANGE",
            "confidence": "high",
            "reason_class": "SESSION_TRANSITION",
            "trade_action": "BUY",
        })


def test_malformed_provider_response_fails_open(monkeypatch):
    class BrokenResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return b'{"choices":[{"message":{"content":"not json"}}]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: BrokenResponse())
    result = classify_snapshot(valid_snapshot(), "redacted-test-only")
    assert result["status"] == "NO_WAKE"
    assert result["marker"] is None
    assert result["reason"] == "JSONDecodeError"
