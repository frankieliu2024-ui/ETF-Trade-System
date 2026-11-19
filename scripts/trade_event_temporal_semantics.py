from __future__ import annotations

from datetime import datetime
from typing import Any


def _date_from_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


def normalize_trade_event_temporal_fields(event: dict, *, strict: bool = True) -> dict:
    """Keep execution day (when the trade happened) separate from confirmation day."""
    executed = event.get("executed_at_beijing") or event.get("executed_at") or event.get("trade_time_beijing") or event.get("trade_time")
    executed_date = _date_from_timestamp(executed)
    execution_date = _date_from_timestamp(event.get("execution_date"))
    if executed_date and execution_date and execution_date != executed_date and strict:
        raise ValueError(f"execution_date_mismatch:expected={executed_date}:actual={execution_date}")
    if executed_date:
        event["execution_date"] = executed_date
    elif not execution_date:
        event["execution_date"] = _date_from_timestamp(event.get("confirmed_at_beijing"))

    confirmation_date = _date_from_timestamp(event.get("confirmation_date"))
    if not confirmation_date:
        event["confirmation_date"] = _date_from_timestamp(event.get("confirmed_at_beijing"))
    return event

