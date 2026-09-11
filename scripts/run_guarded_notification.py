from __future__ import annotations

import argparse
import json
import sys

from notification_materiality_guard import notification_evidence_error
from notification_center import notification_id_for


def _guarded_persist(original):
    def guarded(event: dict, *, policy: str):
        error = notification_evidence_error(event)
        if error:
            return {
                "status": "REJECTED_NO_COMPARABLE_EVIDENCE",
                "detail": error,
                "title": event.get("title"),
            }
        return original(event, policy=policy)

    return guarded


def _batch_event(market: str) -> dict | None:
    """Collect the real market candidates before the sole irreversible send."""
    import market_notification_common as common
    if market in {"a-share", "apac"}:
        import send_regional_session_summary as regional
        import send_market_shock_notification as shock
        events = [
            regional._a_share_event() if market == "a-share" else regional._apac_event(),
            shock._legacy._a_share_candidate() if market == "a-share" else shock._legacy._context_candidate("asia"),
        ]
    else:
        import send_us_session_summary as summary
        import send_market_shock_notification as shock
        events = [summary.build_event(), shock._legacy._context_candidate("us")]
    events = [event for event in events if event]
    if not events:
        return None
    first = events[0]
    ctx = dict(first.get("confirmation_context") or {})
    date = str(ctx.get("market_date") or ctx.get("us_market_date") or "")
    session = str(ctx.get("session") or ctx.get("session_node") or ctx.get("market_phase") or "")
    direction = str(ctx.get("direction") or "")
    family = str(ctx.get("fact_family") or ctx.get("event_category") or "")
    key = (market.upper(), date, session.upper(), direction.upper(), family.upper())
    if not all(key) or any(
        (str((event.get("confirmation_context") or {}).get("market_date") or ""),
         str((event.get("confirmation_context") or {}).get("us_market_date") or ""),
         str((event.get("confirmation_context") or {}).get("session") or
             (event.get("confirmation_context") or {}).get("session_node") or
             (event.get("confirmation_context") or {}).get("market_phase") or ""),
         str((event.get("confirmation_context") or {}).get("direction") or ""),
         str((event.get("confirmation_context") or {}).get("fact_family") or
             (event.get("confirmation_context") or {}).get("event_category") or "").upper()) != key[1:]
        for event in events
    ):
        return first
    merged = dict(first)
    merged["source_event_id"] = "aggregate:" + ":".join(key)
    merged["notification_id"] = notification_id_for(merged)
    merged["content"] = "\n\n".join(str(event.get("content") or "") for event in events if event.get("content"))
    merged["confirmation_context"] = dict(ctx)
    merged["confirmation_context"]["constituent_events"] = [
        {"source_event_id": event.get("source_event_id") or event.get("key"),
         "security_code": event.get("security_code"),
         "event_type": event.get("event_type") or event.get("type")}
        for event in events
    ]
    merged["confirmation_context"]["aggregation_key"] = list(key)
    return merged

def _run_batch(market: str) -> int:
    event = _batch_event(market)
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    error = notification_evidence_error(event)
    if error:
        print(json.dumps({"status": "REJECTED_NO_COMPARABLE_EVIDENCE", "detail": error}, ensure_ascii=False))
        return 0
    import market_notification_common as common
    result = common.persist_and_send(event, policy="canonical bounded market batch")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0

def _run_center(mode: str)  int:
    import notification_center as center

    if mode != "channel-test":
        event = center.choose_event(mode)
        error = notification_evidence_error(event)
        if error:
            print(json.dumps({"status": "REJECTED_NO_COMPARABLE_EVIDENCE", "detail": error, "title": (event or {}).get("title")}, ensure_ascii=False))
            return 0
        original_choose = center.choose_event
        center.choose_event = lambda requested: event if requested == mode else original_choose(requested)
    sys.argv = ["notification_center.py", "--mode", mode]
    return center.main()


def _run_regional(market: str) -> int:
    import market_notification_common as common
    import send_regional_session_summary as producer

    producer.persist_and_send = _guarded_persist(common.persist_and_send)
    sys.argv = ["send_regional_session_summary.py", "--market", market]
    return producer.main()


def _run_shock(market: str) -> int:
    import market_notification_common as common
    import send_market_shock_notification as producer

    producer._legacy.persist_and_send = _guarded_persist(common.persist_and_send)
    sys.argv = ["send_market_shock_notification.py", "--market", market]
    return producer.main()


def _run_us() -> int:
    import market_notification_common as common
    import send_us_session_summary as producer

    producer.persist_and_send = _guarded_persist(common.persist_and_send)
    sys.argv = ["send_us_session_summary.py"]
    return producer.main()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("family", choices=["center", "regional", "shock", "us", "batch"])
    parser.add_argument("--mode", choices=["event", "close", "close-test", "channel-test"], default="event")
    parser.add_argument("--market", choices=["a-share", "apac", "asia", "us"])
    args = parser.parse_args()

    if args.family == "center":
        return _run_center(args.mode)
    if args.family == "batch":
        if args.market not in {"a-share", "apac", "us"}:
            parser.error("batch requires --market a-share|apac|us")
        return _run_batch(args.market)
    if args.family == "regional":
        if args.market not in {"a-share", "apac"}:
            parser.error("regional requires --market a-share|apac")
        return _run_regional(args.market)
    if args.family == "shock":
        if args.market not in {"a-share", "asia", "us"}:
            parser.error("shock requires --market a-share|asia|us")
        return _run_shock(args.market)
    return _run_us()


if __name__ == "__main__":
    raise SystemExit(main())
