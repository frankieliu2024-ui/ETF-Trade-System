from __future__ import annotations
from datetime import datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

def review_due_state(review_date: str, current_date: str, now: datetime, due_time: str = "20:30") -> str:
    if not review_date or not current_date or review_date < current_date:
        return "PRIOR_DAY_OVERDUE"
    if review_date > current_date:
        return "DUE_OR_OVERDUE"
    hour, minute = (int(part) for part in due_time.split(":", 1))
    return "NOT_DUE_TODAY" if now.astimezone(SHANGHAI).time() < time(hour, minute) else "DUE_OR_OVERDUE"
