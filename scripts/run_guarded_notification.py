from __future__ import annotations

import argparse
import json
import os
import sys
import time

from notification_materiality_guard import notification_evidence_error


REPORT_RETRY_DELAYS_SECONDS = (2.0, 8.0)


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


def _pulse_object_label(item: dict) -> str:
    asset = str(item.get("asset") or "").strip()
    code = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    direction = str(item.get("direction") or "").strip()
    if asset:
        return f"{asset}（方向：{direction}）" if direction else asset
    if code and name:
        return f"{code} {name}"
    return code or name or str(item.get("candidate_signature") or "").strip()


def _pulse_event(source: str, label: str, before: dict, after: dict) -> dict | None:
    before_objects = sorted(
        _pulse_object_label(x)
        for x in (before.get("candidates") or before.get("observations") or [])
        if isinstance(x, dict)
    )
    after_objects = sorted(
        _pulse_object_label(x)
        for x in (after.get("candidates") or after.get("observations") or [])
        if isinstance(x, dict)
    )
    before_fact = {
        "identity": before.get("identity"),
        "fact_time_beijing": before.get("fact_time_beijing"),
        "status": before.get("status"),
        "coverage_status": before.get("coverage_status"),
        "candidate_count": before.get("candidate_count"),
        "observation_count": before.get("observation_count"),
        "objects": before_objects,
    }
    after_fact = {
        "identity": after.get("identity"),
        "fact_time_beijing": after.get("fact_time_beijing"),
        "status": after.get("status"),
        "coverage_status": after.get("coverage_status"),
        "candidate_count": after.get("candidate_count"),
        "observation_count": after.get("observation_count"),
        "objects": after_objects,
    }
    if before_fact == after_fact:
        return None
    added = sorted(set(after_objects) - set(before_objects))
    removed = sorted(set(before_objects) - set(after_objects))
    changed = sorted(set(before_objects) & set(after_objects))
    evidence_time = str(after.get("formed_at_beijing") or after.get("fact_time_beijing") or "")
    change_lines = []
    if added:
        change_lines.append("新增对象：" + "、".join(added))
    if removed:
        change_lines.append("消失对象：" + "、".join(removed))
    if changed:
        change_lines.append("延续对象：" + "、".join(changed))
    if not change_lines:
        change_lines.append("对象集合未变，但状态、覆盖或数量发生变化。")
    content = (
        f"【核心结论】{label}证据发生可比变化。\n\n"
        f"【证据变化】\n"
        f"证据时间：{evidence_time}\n"
        f"前一脉冲：状态={before_fact['status']}；覆盖={before_fact['coverage_status']}；"
        f"对象数={before_fact['candidate_count'] or before_fact['observation_count'] or 0}\n"
        f"当前脉冲：状态={after_fact['status']}；覆盖={after_fact['coverage_status']}；"
        f"对象数={after_fact['candidate_count'] or after_fact['observation_count'] or 0}\n"
        + "\n".join(change_lines)
        + "\n\n"
        f"【对ETF系统的影响】该变化用于下一正式判断逐对象核对方向、时机、风险和资本影响；"
        f"不能单独授予Trial、Confirm、买入、卖出或调仓权限。\n\n"
        f"【当前动作】无需自动交易；下一正式判断必须核对上述对象与持仓、观察ETF、Discovery和现金的竞争关系。\n\n"
        f"【数据与边界】本通知仅说明脉冲事实发生可比变化，不等同于交易结论或实际成交。"
    )
    return {
        "key": f"pulse:{source}:{after.get('identity')}",
        "event_type": "PULSE_FACT_UPDATE",
        "source": source,
        "title": f"【市场异动】{label}证据发生可比变化",
        "content": content,
        "user_action": "无需自动交易；下一正式判断逐对象核对变化及其对持仓、观察、Discovery和现金竞争的影响。",
        "confirmation_context": {
            "evidence_time_beijing": evidence_time,
            "before_fact": json.dumps(before_fact, ensure_ascii=False, sort_keys=True),
            "after_fact": json.dumps(after_fact, ensure_ascii=False, sort_keys=True),
            "applicable_object": label,
        },
    }


def _pulse_candidates_from_payloads(payloads: dict[str, dict]) -> list[dict]:
    candidates = []
    for source, label in (("discovery_pulse", "Discovery"), ("hyperliquid_pulse", "Hyperliquid")):
        payload = payloads.get(source) or {}
        history = []
        days = payload.get("days") if isinstance(payload, dict) else None
        if isinstance(days, dict):
            for entries in days.values():
                if isinstance(entries, list):
                    history.extend(x for x in entries if isinstance(x, dict))
        latest = payload.get("latest") if isinstance(payload, dict) else None
        if isinstance(latest, dict) and latest.get("identity"):
            history.append(latest)
        unique = {}
        for item in history:
            identity = str(item.get("identity") or "").strip()
            if identity:
                unique[identity] = item
        ordered = sorted(unique.values(), key=lambda x: str(x.get("formed_at_beijing") or ""))
        if len(ordered) >= 2:
            event = _pulse_event(source, label, ordered[-2], ordered[-1])
            if event:
                candidates.append(event)
    return candidates


def _pulse_candidates() -> list[dict]:
    """Build comparable Discovery/Hyperliquid pulse events from canonical indexes."""
    from notification_center import ROOT, read_json

    return _pulse_candidates_from_payloads({
        "discovery_pulse": read_json(ROOT / "data" / "state" / "discovery_pulse_index.json", {}),
        "hyperliquid_pulse": read_json(ROOT / "data" / "state" / "hyperliquid_pulse_index.json", {}),
    })


def _pulse_test_candidates() -> list[dict]:
    """Build synthetic pulses without reading or writing production pulse indexes.

    Each manual workflow run gets a unique synthetic identity so the test entry
    can exercise the same send path repeatedly without weakening production
    idempotency for real pulse facts.
    """
    run_id = str(os.environ.get("PULSE_TEST_RUN_ID") or "manual").strip()
    suffix = f"_{run_id}"
    base = {
        "status": "READY",
        "coverage_status": "COMPLETE",
        "candidate_count": 2,
        "observation_count": 1,
        "fact_time_beijing": "2026-11-06T15:20:00+08:00",
    }
    return _pulse_candidates_from_payloads({
        "discovery_pulse": {
            "latest": {**base, "identity": f"PULSE_TEST_DISCOVERY_AFTER{suffix}", "formed_at_beijing": "2026-11-06T15:20:00+08:00",
                       "candidate_count": 3, "candidates": [{"candidate_signature": "513630::OBSERVATION_FULL_EVALUATION"},
                                                            {"candidate_signature": "159567::OBSERVATION_FULL_EVALUATION"},
                                                            {"candidate_signature": "520550::OBSERVATION_FULL_EVALUATION"}]},
            "days": {"test": [{**base, "identity": f"PULSE_TEST_DISCOVERY_BEFORE{suffix}", "formed_at_beijing": "2026-11-06T15:19:00+08:00",
                               "candidates": [{"candidate_signature": "513630::OBSERVATION_FULL_EVALUATION"},
                                              {"candidate_signature": "159567::OBSERVATION_FULL_EVALUATION"}]}]},
        },
        "hyperliquid_pulse": {
            "latest": {**base, "identity": f"PULSE_TEST_HYPERLIQUID_AFTER{suffix}", "formed_at_beijing": "2026-11-06T15:20:30+08:00",
                       "candidate_count": 2, "observations": [{"asset": "BABA", "direction": "negative"},
                                                              {"asset": "XBI", "direction": "positive"}]},
            "days": {"test": [{**base, "identity": f"PULSE_TEST_HYPERLIQUID_BEFORE{suffix}", "formed_at_beijing": "2026-11-06T15:19:30+08:00",
                               "observations": [{"asset": "BABA", "direction": "negative"}]}]},
        },
    })




def _collect_candidates(market: str) -> list[dict]:
    if market == "pulse":
        return _pulse_candidates()
    if market == "pulse-test":
        return _pulse_test_candidates()
    if market in {"a-share", "apac"}:
        import send_regional_session_summary as regional
        import send_market_shock_notification as shock
        return [event for event in (
            regional._a_share_event() if market == "a-share" else regional._apac_event(),
            shock._legacy._a_share_candidate() if market == "a-share" else shock._legacy._context_candidate("asia"),
        ) if event]
    import send_us_session_summary as summary
    import send_market_shock_notification as shock
    return [event for event in (summary.build_event(), shock._legacy._context_candidate("us")) if event]


def _run_batch(market: str) -> int:
    import market_notification_common as common
    events = common.aggregate_candidate_events(_collect_candidates(market))
    if not events:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    results = []
    for event in events:
        error = notification_evidence_error(event)
        if error:
            results.append({"status": "REJECTED_NO_COMPARABLE_EVIDENCE", "detail": error})
            continue
        results.append(common.persist_and_send(event, policy="canonical bounded market notification batch"))
    print(json.dumps({"status": "BATCHED", "results": results}, ensure_ascii=False))
    return 1 if any(result.get("status") == "CREATED" for result in results) else 0


def _latest_failed_report_response(center, event: dict) -> dict:
    source = str(event.get("source_event_id") or event.get("key") or "")
    if not source:
        return {}
    state = center.read_json(center.STATE / "notification_center.json", {})
    candidates = [
        item for item in (state.get("notifications") or [])
        if str(item.get("source_event_id") or "") == source
        and str(item.get("event_type") or "") == "REPORT_DELIVERY_REQUEST"
        and str(item.get("lifecycle_status") or "").upper() == "FAILED"
    ]
    if not candidates:
        return {}
    candidates.sort(key=lambda item: str(item.get("last_attempted_at") or item.get("created_at") or ""))
    return dict(candidates[-1].get("response") or {})


def _report_retryable_transport_failure(center, event: dict) -> bool:
    response = _latest_failed_report_response(center, event)
    error = str(response.get("error") or "")
    message = str(response.get("message") or "")
    lowered = message.lower()
    if error == "URLError" and "handshake operation timed out" in lowered:
        return True
    if error == "HTTPError" and any(f"http error {status}:" in lowered for status in (502, 503, 504)):
        return True
    return False


def _run_center(mode: str, formal_content_file: str | None = None) -> int:
    import notification_center as center

    if formal_content_file:
        print(json.dumps({
            "status": "FAILED",
            "error": "Formal Decision direct content projection is disabled; use canonical completion event",
        }, ensure_ascii=False))
        return 1

    event = None
    if mode != "channel-test":
        event = center.choose_event(mode)
        error = notification_evidence_error(event)
        if error:
            print(json.dumps({"status": "REJECTED_NO_COMPARABLE_EVIDENCE", "detail": error, "title": (event or {}).get("title")}, ensure_ascii=False))
            return 0
        original_choose = center.choose_event
        center.choose_event = lambda requested: event if requested == mode else original_choose(requested)
    sys.argv = ["notification_center.py", "--mode", mode]
    result = center.main()
    if (
        result == 0
        or mode != "event"
        or not event
        or str(event.get("event_type") or "") != "REPORT_DELIVERY_REQUEST"
    ):
        return result

    for retry_number, delay in enumerate(REPORT_RETRY_DELAYS_SECONDS, start=1):
        if not _report_retryable_transport_failure(center, event):
            break
        print(json.dumps({
            "status": "RETRYING_REPORT_DELIVERY",
            "retry_number": retry_number,
            "delay_seconds": delay,
            "source_event_id": event.get("source_event_id") or event.get("key"),
        }, ensure_ascii=False))
        time.sleep(delay)
        result = center.main()
        if result == 0:
            break
    return result


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
    parser.add_argument("--formal-content-file")
    parser.add_argument("--market", choices=["a-share", "apac", "asia", "us", "pulse-test"])
    args = parser.parse_args()

    if args.family == "center":
        return _run_center(args.mode, args.formal_content_file)
    if args.family == "batch":
        if args.market not in {"a-share", "apac", "us", "pulse", "pulse-test"}:
            parser.error("batch requires --market a-share|apac|us|pulse")
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
