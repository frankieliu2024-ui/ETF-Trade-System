from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_due_time(due_time: object) -> Optional[time]:
    if not isinstance(due_time, str):
        return None
    parts = due_time.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hour, minute = (int(part) for part in parts)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def configured_review_due_time(policy: dict) -> Optional[str]:
    scheduled = policy.get("scheduled_trade_review")
    if not isinstance(scheduled, dict):
        return None
    due_time = scheduled.get("due_time")
    return due_time if parse_due_time(due_time) is not None else None


def review_due_state(review_date: str, current_date: str, now: datetime, due_time: object) -> str:
    parsed = parse_due_time(due_time)
    if parsed is None:
        return "INVALID_CONFIG"
    if not review_date or not current_date or review_date < current_date:
        return "PRIOR_DAY_OVERDUE"
    if review_date > current_date:
        return "DUE_OR_OVERDUE"
    return "NOT_DUE_TODAY" if now.astimezone(SHANGHAI).time() < parsed else "DUE_OR_OVERDUE"
