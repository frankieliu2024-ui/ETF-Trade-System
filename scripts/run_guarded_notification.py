from __future__ import annotations

import argparse
import json
import sys

from notification_materiality_guard import notification_evidence_error


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


def _run_center(mode: str) -> int:
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
    parser.add_argument("family", choices=["center", "regional", "shock", "us"])
    parser.add_argument("--mode", choices=["event", "close", "close-test", "channel-test"], default="event")
    parser.add_argument("--market", choices=["a-share", "asia", "us"])
    args = parser.parse_args()

    if args.family == "center":
        return _run_center(args.mode)
    if args.family == "regional":
        if args.market not in {"a-share", "asia"}:
            parser.error("regional requires --market a-share|asia")
        return _run_regional(args.market)
    if args.family == "shock":
        if args.market not in {"a-share", "asia", "us"}:
            parser.error("shock requires --market a-share|asia|us")
        return _run_shock(args.market)
    return _run_us()


if __name__ == "__main__":
    raise SystemExit(main())
