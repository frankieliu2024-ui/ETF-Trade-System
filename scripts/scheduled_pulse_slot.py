from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")

def _expand_cron_field(field: str, minimum: int, maximum: int) -> list[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part: continue
        if "/" in part:
            base, step_text = part.split("/", 1); step = int(step_text)
            if base == "*": start, end = minimum, maximum
            elif "-" in base: start_text, end_text = base.split("-", 1); start, end = int(start_text), int(end_text)
            else: start = end = int(base)
            values.update(range(start, end + 1, step))
        elif "-" in part:
            start_text, end_text = part.split("-", 1); values.update(range(int(start_text), int(end_text) + 1))
        elif part == "*": values.update(range(minimum, maximum + 1))
        else: values.add(int(part))
    return sorted(value for value in values if minimum <= value <= maximum)

def resolve_scheduled_pulse(scheduled_cron: str, now: datetime, cadence_seconds: int = 600) -> dict:
    """Resolve one delivery event to its latest eligible natural pulse slot."""
    schedule = str(scheduled_cron or "").strip()
    result = {"scheduled_cron": schedule, "scheduled_slot_at": None, "schedule_delay_seconds": None, "schedule_delay_class": "NOT_SCHEDULED" if not schedule else "UNKNOWN", "natural_pulse_identity": None, "eligible": not bool(schedule)}
    if not schedule: return result
    try:
        minute_field, hour_field, _day, _month, dow_field = schedule.split()
        minutes = _expand_cron_field(minute_field, 0, 59); hours = _expand_cron_field(hour_field, 0, 23); weekdays = _expand_cron_field(dow_field, 0, 7)
        utc_now = now.astimezone(timezone.utc); candidates: list[datetime] = []
        for offset in range(0, 2):
            day = utc_now.date() - timedelta(days=offset); cron_weekday = (day.weekday() + 1) % 7
            if cron_weekday not in weekdays and not (cron_weekday == 0 and 7 in weekdays): continue
            for hour in hours:
                for minute in minutes:
                    candidate = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour, minute=minute)
                    if candidate <= utc_now: candidates.append(candidate)
        slot = max(candidates) if candidates else None
        if slot is None: return result
        delay = max(0, int((utc_now - slot).total_seconds())); slot_local = slot.astimezone(BEIJING)
        delay_class = "SEVERELY_DELAYED" if delay >= int(cadence_seconds) else "BOUNDED_DELAY"
        result.update({"scheduled_slot_at": slot_local.isoformat(timespec="seconds"), "schedule_delay_seconds": delay, "schedule_delay_class": delay_class, "natural_pulse_identity": f"slot:{slot_local.isoformat(timespec='seconds')}", "eligible": delay_class == "BOUNDED_DELAY"})
        return result
    except (TypeError, ValueError): return result
