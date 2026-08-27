"""Lightweight shared runtime guard for market-data quality and fallback decisions.

This module validates provider facts only. It never creates trading permissions or actions.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any


def classify_provider_failure(error: object) -> str:
    text = str(error or "").lower()
    if any(token in text for token in (
        "timeout", "timed out", "connection", "http 429", "http 5", "temporary",
        "temporarily", "upstream", "reset by peer", "service unavailable",
    )):
        return "TRANSIENT"
    if any(token in text for token in (
        "unsupported", "does not support", "unknown thscode", "unknown symbol", "invalid symbol",
        "invalid mapping", "code mismatch", "fund not found", "not found",
        "no exact item",
    )):
        return "STRUCTURAL"
    if any(token in text for token in (
        "missing", "stale", "wrong date", "timestamp", "freshness", "ohlc",
        "negative", "volume", "amount", "non-remote", "quality",
    )):
        return "QUALITY"
    return "UNKNOWN"


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp_ms(row: dict) -> int | None:
    value = row.get("provider_timestamp_ms")
    if value in (None, "", 0, "0"):
        value = row.get("provider_timestamp")
    try:
        if value in (None, "", 0, "0"):
            return None
        number = float(value)
        return int(number * 1000 if number < 10_000_000_000 else number)
    except (TypeError, ValueError):
        return None


def validate_market_row(
    row: dict,
    expected_code: str,
    expected_name: str | None = None,
    *,
    market_date: str | None = None,
    now: datetime | None = None,
    runtime_policy: dict | None = None,
    direct_only: bool = True,
    allow_auction_partial: bool = True,
) -> tuple[bool, str]:
    """Return (valid, reason) for one normalized provider row."""
    code = str(expected_code)
    seen = {str(row.get(key, "")).strip() for key in ("symbol", "code", "thscode", "provider_symbol")}
    if not any(item == code or item.split(".")[0] == code for item in seen if item):
        return False, f"target code mismatch: expected={code} seen={sorted(seen)}"
    if expected_name is not None and not str(row.get("provider_name") or row.get("name") or expected_name).strip():
        return False, "missing security name"
    ts_ms = _timestamp_ms(row)
    if ts_ms is None:
        return False, "missing provider timestamp"
    now_dt = now or datetime.now(timezone.utc)
    provider_dt = datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
    if market_date and provider_dt.astimezone(now_dt.tzinfo or timezone.utc).date().isoformat() != market_date:
        return False, f"wrong provider date: {provider_dt.isoformat()}"
    policy = runtime_policy or {}
    fresh_max = int(policy.get("fresh_max_age_seconds", 900))
    degraded_max = int(policy.get("degraded_max_age_seconds", 1500))
    age = max(0.0, (now_dt - provider_dt).total_seconds())
    if age > degraded_max:
        return False, f"stale provider timestamp age={age:.1f}s"
    phase = str(row.get("market_phase") or "")
    missing_fields = [
        key for key in ("open", "high", "low", "close", "prev_close")
        if row.get(key) in (None, "", "-")
    ]
    auction_partial = (
        allow_auction_partial
        and phase == "OPENING_CALL_AUCTION"
        and set(missing_fields).issubset({"open", "high", "low"})
        and row.get("close") not in (None, "", "-")
        and row.get("prev_close") not in (None, "", "-")
    )
    explicit_halt = (
        str(row.get("trading_status") or "").upper() in {"SUSPENDED", "HALTED"}
        and row.get("tradable") is False
        and row.get("close") not in (None, "", "-")
        and row.get("prev_close") not in (None, "", "-")
    )
    if missing_fields and not (auction_partial or explicit_halt):
        return False, "missing required market fields: " + ",".join(missing_fields)
    close = _as_float(row.get("close"))
    prev_close = _as_float(row.get("prev_close"))
    if close is None or close <= 0 or prev_close is None or prev_close <= 0:
        return False, "last/prev_close must be positive"
    if not (auction_partial or explicit_halt):
        values = [_as_float(row.get(key)) for key in ("open", "high", "low")]
        if any(value is None for value in values):
            return False, "OHLC contains non-numeric value"
        opening, high, low = values
        if high < max(opening, low, close) or low > min(opening, high, close):
            return False, "invalid OHLC relationship"
    volume = _as_float(row.get("volume"))
    amount = _as_float(row.get("amount"))
    if volume is None or amount is None or volume < 0 or amount < 0:
        return False, "volume/amount must be non-negative"
    if direct_only and ("proxy" in str(row.get("provider", "")).lower() or "proxy" in str(row.get("quality_status", "")).lower()):
        return False, "direct-only object cannot pass with proxy data"
    row["provider_timestamp_ms"] = ts_ms
    row["as_of_beijing"] = provider_dt.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    row["freshness_at_validation_seconds"] = round(age, 3)
    row["freshness_status"] = "FRESH" if age <= fresh_max else "DEGRADED"
    return True, ""


def annotate_failure(error: object, *, provider: str = "") -> dict:
    reason = str(error or "")[-500:]
    return {
        "failure_class": classify_provider_failure(reason),
        "failure_reason": reason,
        "provider": provider,
    }


def update_structural_health(
    health: dict,
    provider_key: str,
    *,
    failure_class: str,
    reason: str,
    now: datetime,
    threshold: int = 2,
) -> dict:
    """Keep a tiny session-scoped failure memory; callers persist it in existing health."""
    entry = dict((health.get("provider_health") or {}).get(provider_key) or {})
    if failure_class == "STRUCTURAL":
        entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
        entry["last_failure_at"] = now.isoformat(timespec="seconds")
        entry["reason"] = reason[-200:]
        entry["status"] = "SESSION_BYPASS" if entry["consecutive_failures"] >= threshold else "ACTIVE"
    else:
        entry["status"] = "ACTIVE"
        entry["consecutive_failures"] = 0
    entry["failure_class"] = failure_class
    health.setdefault("provider_health", {})[provider_key] = entry
    return health
