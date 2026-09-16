from __future__ import annotations

from datetime import timedelta


TECH_GROWTH_EPISODE_CODES = frozenset({"000688", "588000", "159781", "399006"})
TECH_GROWTH_EPISODE_ID = "A_SHARE_TECH_GROWTH_EPISODE"
MARKET_ANOMALY_CATEGORIES = frozenset({"SUDDEN", "EXTREME", "REVERSAL", "DIVERGENCE"})

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
    event = _annotate_structure_episode(_original_build_a_share_event(*args, **kwargs))
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


def _structure_cluster_id(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    code = str(event.get("security_code") or ctx.get("security_code") or "")
    category = str(ctx.get("event_category") or "")
    event_type = str(event.get("event_type") or event.get("type") or "")
    if (event_type not in {"MARKET_SHOCK_ALERT", "MARKET_VALUE_ALERT"}):
        return ""
    if (str(ctx.get("market") or "").upper() == "A_SHARE"
            and code in TECH_GROWTH_EPISODE_CODES
            and category in MARKET_ANOMALY_CATEGORIES):
        return TECH_GROWTH_EPISODE_ID
    return ""


def _annotate_structure_episode(event: dict) -> dict:
    cluster = _structure_cluster_id(event)
    if not cluster:
        return event
    ctx = dict(event.get("confirmation_context") or {})
    ctx["structure_cluster_id"] = cluster
    ctx["structure_cluster_members"] = sorted(TECH_GROWTH_EPISODE_CODES)
    ctx["structure_cluster_scope"] = "EXPLICIT_SAME_EPISODE_ONLY"
    event["confirmation_context"] = ctx
    return event


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
    event = _annotate_structure_episode(_original_build_context_event(*args, **kwargs))
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


def _recent_duplicate(event: dict) -> bool:
    """Bound repeated market-anomaly attention within one object/episode."""
    event = _annotate_structure_episode(event)
    ctx = event.get("confirmation_context") or {}
    category = str(ctx.get("event_category") or "")
    if category not in MARKET_ANOMALY_CATEGORIES:
        return _original_recent_duplicate(event)
    state = _legacy.read_json(_legacy.STATE / "notification_center.json", {})
    code = str(event.get("security_code") or "")
    cluster = str(ctx.get("structure_cluster_id") or "")
    direction = str(ctx.get("direction") or "")
    market_date = str(ctx.get("market_date") or "")
    if not market_date:
        return _original_recent_duplicate(event)
    matching = []
    for item in state.get("notifications") or []:
        if str(item.get("event_type") or "") not in {"MARKET_SHOCK_ALERT", "MARKET_VALUE_ALERT"}:
            continue
        old = item.get("confirmation_context") or {}
        old_event = dict(item)
        old_event["confirmation_context"] = old
        old_cluster = _structure_cluster_id(old_event)
        if str(old.get("market_date") or "") != market_date or str(old.get("direction") or "") != direction:
            continue
        if str(old.get("event_category") or old.get("shock_severity") or "") != category:
            continue
        if cluster:
            if old_cluster != cluster and str(item.get("security_code") or "") != code:
                continue
        elif str(item.get("security_code") or "") != code:
            continue
        matching.append(item)
    if not matching:
        return _original_recent_duplicate(event)
    if len(matching) >= 2:
        return True
    previous = matching[-1].get("confirmation_context") or {}
    old_mag = abs(_legacy.number(previous.get("event_magnitude_pct")) or _legacy.number(previous.get("day_change_pct")) or _legacy.number(previous.get("phase_metric_change_pct")) or 0.0)
    new_mag = abs(_legacy.number(ctx.get("event_magnitude_pct")) or _legacy.number(ctx.get("day_change_pct")) or _legacy.number(ctx.get("phase_metric_change_pct")) or 0.0)
    upgrade_needed = max(_legacy.UPGRADE_MIN_ABS_PCT, old_mag * _legacy.UPGRADE_RELATIVE)
    return new_mag < old_mag + upgrade_needed


# Patch only presentation/de-duplication around the legacy detector. Detection
# thresholds, event eligibility and all non-market notification families stay unchanged.
_legacy._build_a_share_event = _build_a_share_event
_legacy._build_context_event = _build_context_event
_legacy._recent_duplicate = _recent_duplicate
main = _legacy.main


if __name__ == "__main__":
    raise SystemExit(main())
