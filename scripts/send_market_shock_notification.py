from __future__ import annotations

from datetime import timedelta

try:
    import send_market_shock_notification_legacy as _legacy
    from minute_notification_context import enrich_market_alert_content, minute_notification_note
except ModuleNotFoundError:
    from scripts import send_market_shock_notification_legacy as _legacy
    from scripts.minute_notification_context import enrich_market_alert_content, minute_notification_note

# Re-export legacy names so existing imports keep working.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_original_build_a_share_event = _legacy._build_a_share_event
_original_build_context_event = _legacy._build_context_event
_original_recent_duplicate = _legacy._recent_duplicate


def _build_a_share_event(*args, **kwargs):
    event = _original_build_a_share_event(*args, **kwargs)
    code = str(event.get("security_code") or "")
    note = minute_notification_note(code)
    if note:
        event["content"] = enrich_market_alert_content(event.get("content") or "", code)
        source = str(event.get("source") or "")
        if "minute_context" not in source:
            event["source"] = (source + "+minute_context").strip("+")
        ctx = dict(event.get("confirmation_context") or {})
        ctx["minute_structure_evidence_used"] = True
        ctx["minute_structure_note"] = note
        event["confirmation_context"] = ctx
    return event


def _comparison_level(ctx: dict, category: str) -> float | None:
    if category == "DIVERGENCE":
        return _legacy.number(ctx.get("event_magnitude_pct"))
    level = _legacy.number(ctx.get("day_change_pct"))
    if level is None:
        level = _legacy.number(ctx.get("phase_metric_change_pct"))
    return level


def _recent_same_family_level(event: dict) -> float | None:
    """Return the latest comparable level for an already-sent event family."""
    ctx = event.get("confirmation_context") or {}
    code = str(event.get("security_code") or "")
    category = str(ctx.get("event_category") or "")
    direction = str(ctx.get("direction") or "")
    market_date = str(ctx.get("market_date") or "")
    if not code or not category or not market_date:
        return None

    state = _legacy.read_json(_legacy.STATE / "notification_center.json", {})
    for item in reversed(state.get("notifications") or []):
        if str(item.get("event_type") or "") not in {"MARKET_SHOCK_ALERT", "MARKET_VALUE_ALERT"}:
            continue
        if str(item.get("security_code") or "") != code:
            continue
        old = item.get("confirmation_context") or {}
        if str(old.get("event_category") or old.get("shock_severity") or "") != category:
            continue
        if str(old.get("direction") or "") != direction:
            continue
        if str(old.get("market_date") or "") != market_date:
            continue
        return _comparison_level(old, category)
    return None


def _build_context_event(*args, **kwargs):
    """Keep the legacy detector, but make user-visible upgrade semantics explicit."""
    event = _original_build_context_event(*args, **kwargs)
    ctx = event.get("confirmation_context") or {}
    category = str(ctx.get("event_category") or "")
    current = _comparison_level(ctx, category)
    previous = _recent_same_family_level(event)
    if previous is None or current is None:
        return event

    direction = str(ctx.get("direction") or "")
    if category == "DIVERGENCE":
        progress = current - previous
    elif direction == "DOWN":
        progress = previous - current
    elif direction == "UP":
        progress = current - previous
    else:
        progress = abs(current) - abs(previous)
    if progress <= 0:
        return event

    title = str(event.get("title") or "")
    if category == "DIVERGENCE" and str(ctx.get("market") or "") == "US":
        event["title"] = f"【异动提醒】美股科技分化进一步扩大至{current:.2f}个百分点"
    elif category == "EXTREME" and str(ctx.get("market") or "") == "US":
        name = str(event.get("security_name") or event.get("security_code") or "相关对象")
        event["title"] = f"【异动提醒】{name}波动进一步扩大至{_legacy.pct(current)}"
    else:
        event["title"] = title

    content = str(event.get("content") or "")
    marker = "### 核心结论\n"
    if category == "DIVERGENCE":
        comparison = f"- **较上一同类通知**：{previous:.2f} → {current:.2f}个百分点，进一步扩大{progress:.2f}个百分点。\n"
    else:
        comparison = f"- **较上一同类通知**：{_legacy.pct(previous)} → {_legacy.pct(current)}，变化{progress:.2f}个百分点。\n"
    if marker in content and "较上一同类通知" not in content:
        event["content"] = content.replace(marker, marker + comparison, 1)
    return event


def _covered_by_recent_us_open(event: dict) -> bool:
    """Absorb only the same opening pulse already fully described by the US open alert.

    The fixed/open producer runs before the generic shock producer. If the open
    alert already states both the opening gap and the current extreme level for
    the same direct cash index, a second EXTREME alert seconds later adds no new
    user information. Later material deterioration remains eligible.
    """
    ctx = event.get("confirmation_context") or {}
    if str(ctx.get("market") or "") != "US" or str(ctx.get("event_category") or "") != "EXTREME":
        return False
    code = str(event.get("security_code") or "")
    if code not in {"NDX", "SOX"}:
        return False
    market_date = str(ctx.get("market_date") or "")
    if not market_date:
        return False

    state = _legacy.read_json(_legacy.STATE / "notification_center.json", {})
    for item in reversed(state.get("notifications") or []):
        if str(item.get("event_type") or "") != "US_OPEN_VALUE_ALERT":
            continue
        open_ctx = item.get("confirmation_context") or {}
        if str(open_ctx.get("us_market_date") or "") != market_date:
            continue
        stamp = _legacy.parse_notification_time(item.get("sent_at") or item.get("created_at"))
        if not stamp or not (timedelta(0) <= _legacy.now() - stamp <= timedelta(minutes=_legacy.SUMMARY_ABSORB_MINUTES)):
            return False
        level = _legacy.number(open_ctx.get("tech_change_pct" if code == "NDX" else "semi_change_pct"))
        if level is None or abs(level) < _legacy.THRESHOLDS["us"]["index_extreme"]:
            return False
        direction = str(ctx.get("direction") or "")
        return (direction == "DOWN" and level < 0) or (direction == "UP" and level > 0)
    return False


def _recent_duplicate(event: dict) -> bool:
    if _covered_by_recent_us_open(event):
        return True
    return _original_recent_duplicate(event)


# Patch only presentation/de-duplication around the legacy detector. Detection
# thresholds, event eligibility and all non-market notification families stay unchanged.
_legacy._build_a_share_event = _build_a_share_event
_legacy._build_context_event = _build_context_event
_legacy._recent_duplicate = _recent_duplicate
main = _legacy.main


if __name__ == "__main__":
    raise SystemExit(main())
