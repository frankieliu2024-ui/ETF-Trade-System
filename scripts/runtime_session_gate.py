from __future__ import annotations

import argparse
import json
import os
import subprocess

try:
    from scheduled_pulse_slot import resolve_scheduled_pulse
except ModuleNotFoundError:
    from scripts.scheduled_pulse_slot import resolve_scheduled_pulse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def set_output(key: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


STATE_SYNC_FIELDS = ("account_fact", "formal_decision", "trade_event", "formal_review")
EVIDENCE_REQUEST_TYPES = {"EMERGENCY_EXTERNAL_MARKET_EVIDENCE"}
BUSINESS_DECISION_SOURCE_REQUEST_TYPE = "BUSINESS_DECISION_SOURCE"
REFRESH_REQUEST_TYPES = {"MARKET_QUOTE_REFRESH", "QUERY_TIME_REFRESH", "LIVE_SNAPSHOT_REFRESH"}
EXPLICIT_REFRESH_INTENTS = {"EXPLICIT_LATEST", "MARKET_QUOTE_REFRESH", "QUERY_TIME_REFRESH"}


scheduled_cron_observability = resolve_scheduled_pulse


def parse_runtime_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)


def interaction_scenario_for_time(policy: dict, when: datetime) -> str:
    """Resolve the registered interaction scenario for one Beijing timestamp."""
    local = when.astimezone(SHANGHAI)
    minute = local.hour * 60 + local.minute
    for route in ((policy.get("interaction_routing") or {}).get("routes") or []):
        try:
            sh, sm = [int(x) for x in str(route.get("start") or "").split(":", 1)]
            eh, em = [int(x) for x in str(route.get("end") or "").split(":", 1)]
        except (TypeError, ValueError):
            continue
        start_minute = sh * 60 + sm
        end_minute = eh * 60 + em
        if start_minute <= minute <= end_minute:
            scenario = str(route.get("scenario") or "").strip().upper()
            if scenario:
                return scenario
    raise ValueError("runtime policy has no interaction route for current Beijing time")


def normalize_manual_request_session(request: dict, policy: dict, *, now: datetime | None = None) -> dict:
    """Bind one new manual Chat request to its own request time and interaction scenario."""
    normalized = json.loads(json.dumps(request))
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    requested = parse_runtime_time(
        normalized.get("requested_at_beijing")
        or normalized.get("request_time_beijing")
        or normalized.get("requested_at_utc")
    )
    if requested is None:
        requested = current
    normalized["requested_at_beijing"] = requested.isoformat(timespec="seconds")
    derived = interaction_scenario_for_time(policy, requested)
    supplied = str(normalized.get("interaction_scenario") or normalized.get("scenario") or "").strip().upper()
    if supplied and supplied != derived:
        normalized["supplied_interaction_scenario"] = supplied
        normalized["interaction_scenario_reclassified"] = True
    normalized["interaction_scenario"] = derived
    normalized["scenario"] = derived
    return normalized


def classify_live_snapshot_request(request: dict) -> str:
    """Classify one live_snapshot request before any market capture.

    This is a routing classification only. It does not create a producer,
    state store, transport, decision engine or freshness rule.
    """
    request_type = str(request.get("request_type") or "").upper()
    if request_type == BUSINESS_DECISION_SOURCE_REQUEST_TYPE:
        return "BUSINESS_DECISION_SOURCE"
    source = str(request.get("source") or "").upper()
    scenario = str(request.get("interaction_scenario") or "").upper()
    has_state_sync = any(field in request for field in STATE_SYNC_FIELDS)
    has_state_sync = has_state_sync or str(request.get("request_type") or "").upper() in EVIDENCE_REQUEST_TYPES
    has_state_sync = has_state_sync or source == "CHATGPT_USER_BROKER_SCREENSHOT" or scenario == "BROKER_SCREENSHOT_SYNC"
    refresh_bearing = bool(
        request.get("force_refresh") is True
        or request.get("refresh_required") is True
        or request.get("require_post_request_snapshot") is True
        or request.get("wait_for_refresh") is True
        or str(request.get("request_type") or "").upper() in REFRESH_REQUEST_TYPES
        or str(request.get("query_intent") or "").upper() in EXPLICIT_REFRESH_INTENTS and not has_state_sync
    )
    if has_state_sync and refresh_bearing:
        return "HYBRID"
    if has_state_sync:
        return "STATE_SYNC_ONLY"
    return "REFRESH_BEARING"


def _changed_request_files() -> list[Path]:
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        return []
    before = os.environ.get("EVENT_BEFORE", "").strip()
    sha = os.environ.get("GITHUB_SHA", "HEAD").strip()
    if before and subprocess.run(["git", "cat-file", "-e", f"{before}^{{commit}}"], capture_output=True).returncode == 0:
        command = ["git", "diff", "--name-only", before, sha, "--", "requests/live_snapshot"]
    else:
        command = ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha, "--", "requests/live_snapshot"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return [ROOT / name.strip() for name in result.stdout.splitlines() if name.strip().endswith(".json")]


def _push_request_class() -> str:
    classes = []
    policy = load_json(ROOT / "config" / "runtime_policy.json")
    for path in _changed_request_files():
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
            source = str(request.get("source") or request.get("requested_by") or "").upper()
            is_manual_chat = "CHATGPT" in source or "MANUAL" in source
            if is_manual_chat:
                normalized = normalize_manual_request_session(request, policy)
                if normalized.get("interaction_scenario_reclassified"):
                    print(
                        "manual request interaction_scenario reclassified by requested_at_beijing: "
                        f"supplied={normalized.get('supplied_interaction_scenario')} "
                        f"derived={normalized.get('interaction_scenario')}"
                    )
                request = normalized
            classes.append(classify_live_snapshot_request(request))
        except (OSError, json.JSONDecodeError):
            continue
    if not classes:
        return "NOT_APPLICABLE"
    if all(item == "STATE_SYNC_ONLY" for item in classes):
        return "STATE_SYNC_ONLY"
    if any(item == "HYBRID" for item in classes) or len(set(classes)) > 1:
        return "HYBRID"
    return "REFRESH_BEARING"


def scheduled_close_boundary_intent(now: datetime, event_name: str, scheduled_cron: str) -> bool:
    """Recognize a real scheduled pulse that reached the A-share close boundary.

    GitHub may dispatch the ten-minute active-session pulse at 14:59 while the
    explicit 15:00/15:10 cron is delayed or absent. The pulse is a close
    candidate only at the exchange close boundary; the producer must wait until
    15:00 before querying and publishing the close fact.
    """
    minute = now.hour * 60 + now.minute
    return (
        event_name == "schedule"
        and bool(scheduled_cron)
        and now.weekday() < 5
        and 14 * 60 + 57 <= minute <= 15 * 60
    )


def market_phase(minute: int, close_grace_minutes: int = 15) -> str:
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return "OPENING_CALL_AUCTION"
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        return "CONTINUOUS_MORNING"
    if 11 * 60 + 30 < minute < 13 * 60:
        return "MIDDAY_BREAK"
    if 13 * 60 <= minute < 14 * 60 + 57:
        return "CONTINUOUS_AFTERNOON"
    if 14 * 60 + 57 <= minute <= 15 * 60:
        return "CLOSING_CALL_AUCTION"
    if 15 * 60 < minute <= 15 * 60 + max(1, close_grace_minutes):
        return "POST_CLOSE_GRACE"
    return "OUTSIDE_SESSION"


def main() -> int:
    now = datetime.now(SHANGHAI)
    date_text = now.date().isoformat()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    request_class = _push_request_class() if event_name == "push" else "NOT_APPLICABLE"
    scheduled_cron = os.environ.get("SCHEDULED_CRON", "").strip()
    policy = load_json(ROOT / "config" / "runtime_policy.json")
    schedule_observation = resolve_scheduled_pulse(scheduled_cron, now, cadence_seconds=int(policy.get("target_cadence_seconds", 600)))
    scheduled_close_intent = (
        event_name == "schedule"
        and scheduled_cron == "0,10 7 * * 1-5"
    )
    boundary_close_intent = scheduled_close_boundary_intent(now, event_name, scheduled_cron)
    calendar = load_json(ROOT / "config" / "market" / "a_share_trading_calendar_2026.json")

    start = calendar.get("coverage_start", "")
    end = calendar.get("coverage_end", "")
    if not start or not end or not (start <= date_text <= end):
        raise RuntimeError(f"A-share trading calendar does not cover {date_text}: {start}..{end}")

    close_grace_minutes = max(1, int(policy.get("close_grace_seconds", 900)) // 60)
    minute = now.hour * 60 + now.minute
    phase = market_phase(minute, close_grace_minutes)
    if now.weekday() >= 5:
        should_capture, reason = False, "weekend"
    elif date_text in set(calendar.get("closed_dates") or []):
        should_capture, reason = False, "exchange_closed"
    else:
        in_opening_auction = 9 * 60 + 15 <= minute < 9 * 60 + 30
        in_morning = 9 * 60 + 30 <= minute <= 11 * 60 + 30
        in_midday_recovery = 11 * 60 + 30 < minute < 13 * 60
        in_afternoon = 13 * 60 <= minute <= 15 * 60
        in_close_grace = 15 * 60 < minute <= 15 * 60 + close_grace_minutes
        should_capture = in_opening_auction or in_morning or in_midday_recovery or in_afternoon or in_close_grace
        reason = "midday_morning_close_recovery" if in_midday_recovery else ("capture_window" if should_capture else "outside_a_share_capture_window")

    if event_name == "schedule" and schedule_observation["schedule_delay_class"] in {"SEVERELY_DELAYED", "UNKNOWN"}:
        should_capture = False
        reason = "stale_scheduled_pulse"
    elif scheduled_close_intent and should_capture is False and minute >= 15 * 60 and date_text not in set(calendar.get("closed_dates") or []):
        should_capture = True
        phase = "POST_CLOSE_RECOVERY"
        reason = "delayed_scheduled_close_recovery"

    if event_name == "push" and request_class == "STATE_SYNC_ONLY":
        should_capture = False
        reason = "state_sync_only_request"

    close_intent = bool(scheduled_close_intent or boundary_close_intent)
    wait_for_close_boundary_seconds = 0
    if boundary_close_intent and minute < 15 * 60:
        wait_for_close_boundary_seconds = 15 * 60 - (now.hour * 60 * 60 + now.minute * 60 + now.second)

    query_time_refresh = os.environ.get("QUERY_TIME_REFRESH", "").lower() == "true"
    if query_time_refresh and event_name == "push":
        reason = "query_time_refresh_midday_reference" if phase == "MIDDAY_BREAK" else "query_time_refresh_separate_global_path"
    elif event_name == "workflow_dispatch":
        # Preserve the canonical outside-window enum consumed by downstream
        # phase-consistency gates; dispatch observability must not rewrite it.
        if should_capture:
            reason = "manual_dispatch_capture_window"
        elif reason != "outside_a_share_capture_window":
            reason = f"manual_dispatch_{reason}"

    set_output("should_capture", "true" if should_capture else "false")
    set_output("request_class", request_class)
    set_output("reason", reason)
    set_output("market_date", date_text)
    set_output("market_phase", phase)
    set_output("close_intent", "true" if close_intent else "false")
    set_output("wait_for_close_boundary_seconds", str(max(0, wait_for_close_boundary_seconds)))
    for key, value in schedule_observation.items():
        set_output(key, "" if value is None else str(value))
    print(json.dumps({"should_capture": should_capture, "request_class": request_class, "reason": reason, "market_date": date_text, "market_phase": phase, "close_intent": close_intent, "wait_for_close_boundary_seconds": max(0, wait_for_close_boundary_seconds), "captured_at_beijing": now.isoformat(timespec="seconds"), "schedule_observability": schedule_observation}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
