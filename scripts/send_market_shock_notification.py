from __future__ import annotations

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


# Patch only A-share market-value/shock event construction. Other notification families
# (trade confirmation, formal decision, account/system notifications) remain unchanged.
_legacy._build_a_share_event = _build_a_share_event
main = _legacy.main


if __name__ == "__main__":
    raise SystemExit(main())
