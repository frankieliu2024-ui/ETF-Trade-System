from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from rules_version import parse_master_release_file
except ModuleNotFoundError:
    from scripts.rules_version import parse_master_release_file

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime_policy.json"
CALENDAR_PATH = ROOT / "config" / "market" / "a_share_trading_calendar_2026.json"
CURRENT_PATH = ROOT / "data" / "state" / "CURRENT.json"
RUNTIME_HEALTH_PATH = ROOT / "data" / "state" / "runtime_health.json"
CONSISTENCY_PATH = ROOT / "data" / "state" / "system_consistency.json"
QUERY_CONTEXT_PATH = ROOT / "data" / "state" / "query_context.json"
DECISION_CONTEXT_PATH = ROOT / "data" / "state" / "decision_context.json"
MASTER_PATH = ROOT / "ETF规则_MASTER.md"
STATUS_PATH = ROOT / "data" / "state" / "self_healing_status.json"
TZ = ZoneInfo("Asia/Shanghai")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except (TypeError, ValueError):
        return None


def market_open_date(now: datetime, calendar: dict) -> bool:
    day = now.date().isoformat()
    if now.weekday() >= 5:
        return False
    return day not in set(calendar.get("closed_dates") or [])


def in_watch_window(now: datetime) -> bool:
    minute = now.hour * 60 + now.minute
    return (9 * 60 + 15 <= minute <= 11 * 60 + 40) or (13 * 60 <= minute <= 15 * 60 + 15)


def same_day_current_required(now: datetime, current: dict, calendar: dict) -> bool:
    """Require today's canonical A-share CURRENT during an open session."""
    if not market_open_date(now, calendar):
        return False
    minute = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= minute <= 15 * 60 + 15


def same_day_current_missing(now: datetime, current: dict, calendar: dict) -> bool:
    if not same_day_current_required(now, current, calendar):
        return False
    return str(current.get("market_date") or "") != now.date().isoformat()


def current_capture_time(current: dict) -> datetime | None:
    return parse_dt(
        current.get("captured_at")
        or (current.get("data_freshness") or {}).get("captured_at_beijing")
        or (current.get("data_freshness") or {}).get("captured_at")
    )


def master_version() -> str | None:
    release = parse_master_release_file(MASTER_PATH)
    return release.get("version") if release.get("ok") else None


def valid_json(path: Path) -> bool:
    return isinstance(load_json(path), dict)


def previous_status() -> dict:
    data = load_json(STATUS_PATH, {})
    return data if isinstance(data, dict) else {}


def recent_attempts(previous: dict, now: datetime) -> list[str]:
    result: list[str] = []
    cutoff = now - timedelta(hours=1)
    for raw in previous.get("snapshot_refresh_attempts") or []:
        dt = parse_dt(raw)
        if dt and dt >= cutoff:
            result.append(dt.isoformat(timespec="seconds"))
    return result


def assess(now: datetime | None = None) -> dict:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    policy = load_json(POLICY_PATH, {}) or {}
    self_healing = policy.get("self_healing") or {}
    calendar = load_json(CALENDAR_PATH, {}) or {}
    current = load_json(CURRENT_PATH, {}) or {}
    health = load_json(RUNTIME_HEALTH_PATH, {}) or {}
    consistency = load_json(CONSISTENCY_PATH, {}) or {}
    previous = previous_status()
    attempts = recent_attempts(previous, now)

    enabled = bool(self_healing.get("enabled", False))
    trigger_age = int(self_healing.get("watchdog_trigger_age_seconds", 780))
    min_retrigger = int(self_healing.get("minimum_retrigger_seconds", 480))
    max_attempts = int(self_healing.get("max_snapshot_repair_attempts_per_hour", 2))
    open_day = market_open_date(now, calendar)
    watch_window = open_day and in_watch_window(now)
    same_day_required = same_day_current_required(now, current, calendar)
    same_day_missing = same_day_current_missing(now, current, calendar)
    captured = current_capture_time(current)
    age_seconds = int((now - captured).total_seconds()) if captured else None
    health_status = str(health.get("status") or health.get("quality_status") or "UNKNOWN").upper()
    consistency_status = str(consistency.get("status") or "UNKNOWN").upper()
    master_ver = master_version()
    current_ver = str(current.get("rules_version") or "")

    classification = "HEALTHY"
    action = "NONE"
    reason = "runtime state is within self-healing bounds"

    if master_ver is None:
        classification, action, reason = "RULES_VERSION_METADATA_INVALID", "ESCALATE", "MASTER current release metadata cannot be parsed safely"
    elif not current_ver or master_ver != current_ver:
        classification, action, reason = "RULES_VERSION_METADATA_DRIFT", "SYNC_RULES_VERSION_METADATA", f"MASTER={master_ver}, CURRENT={current_ver}"
    elif not enabled:
        classification, action, reason = "DISABLED", "NONE", "self-healing disabled by runtime policy"
    elif consistency_status == "FAIL":
        classification, action, reason = "CONSISTENCY_REGRESSION", "ESCALATE", "system consistency has a hard failure; automatic code/rule repair is forbidden"
    elif same_day_missing:
        last_trigger = parse_dt(previous.get("last_snapshot_refresh_trigger_at"))
        seconds_since_trigger = int((now - last_trigger).total_seconds()) if last_trigger else None
        if len(attempts) >= max_attempts:
            classification, action, reason = "PERSISTENT_RUNTIME_FAILURE", "ESCALATE", "same-day CURRENT is missing and snapshot repair is already at the hourly attempt limit"
        elif seconds_since_trigger is not None and seconds_since_trigger < min_retrigger:
            classification, action, reason = "REPAIR_COOLDOWN", "NONE", f"same-day CURRENT is missing but snapshot refresh is in cooldown ({seconds_since_trigger}s < {min_retrigger}s)"
        else:
            classification, action, reason = "SAME_DAY_CURRENT_MISSING", "REFRESH_SNAPSHOT", f"market_date={current.get('market_date')!r}, expected={now.date().isoformat()}"
    elif watch_window and (captured is None or age_seconds is None or age_seconds > trigger_age or health_status == "FAILED"):
        last_trigger = parse_dt(previous.get("last_snapshot_refresh_trigger_at"))
        seconds_since_trigger = int((now - last_trigger).total_seconds()) if last_trigger else None
        if len(attempts) >= max_attempts:
            classification, action, reason = "PERSISTENT_RUNTIME_FAILURE", "ESCALATE", f"snapshot refresh already attempted {len(attempts)} time(s) in the last hour"
        elif seconds_since_trigger is not None and seconds_since_trigger < min_retrigger:
            classification, action, reason = "REPAIR_COOLDOWN", "NONE", f"snapshot refresh is in cooldown ({seconds_since_trigger}s < {min_retrigger}s)"
        else:
            classification, action, reason = "SCHEDULE_MISSED_OR_STALE", "REFRESH_SNAPSHOT", f"capture age={age_seconds!r}s, health={health_status}, threshold={trigger_age}s"
    elif not valid_json(QUERY_CONTEXT_PATH) or not valid_json(DECISION_CONTEXT_PATH):
        classification, action, reason = "DERIVED_CONTEXT_INVALID", "REBUILD_DERIVED_CONTEXTS", "query_context or decision_context is missing/invalid JSON"
    return {
        "schema_version": "1.0",
        "checked_at": now.isoformat(timespec="seconds"),
        "mode": "DETERMINISTIC_RUNTIME_SELF_HEALING",
        "enabled": enabled,
        "classification": classification,
        "recommended_action": action,
        "reason": reason,
        "market_open_date": open_day,
        "watch_window_active": watch_window,
        "same_day_current_required": same_day_required,
        "same_day_current_missing": same_day_missing,
        "current_capture_at": captured.isoformat(timespec="seconds") if captured else None,
        "current_capture_age_seconds": age_seconds,
        "runtime_health_status": health_status,
        "system_consistency_status": consistency_status,
        "master_rules_version": master_ver,
        "current_rules_version": current_ver or None,
        "snapshot_refresh_attempts": attempts,
        "last_snapshot_refresh_trigger_at": previous.get("last_snapshot_refresh_trigger_at"),
        "last_safe_repair_at": previous.get("last_safe_repair_at"),
        "safety_boundary": {
            "may_modify_master": False,
            "may_change_trading_permission": False,
            "may_change_provider_policy": False,
            "may_auto_trade": False,
            "may_ai_patch_main": False,
        },
    }


def repair_safe(status: dict, now: datetime | None = None) -> dict:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    action = status.get("recommended_action")
    repaired: list[str] = []
    if action == "SYNC_RULES_VERSION_METADATA":
        current = load_json(CURRENT_PATH, {}) or {}
        version = master_version()
        if version and current.get("rules_version") != version:
            current["rules_version"] = version
            atomic_write_json(CURRENT_PATH, current)
            repaired.append("CURRENT.rules_version")
    status["safe_repairs_applied"] = repaired
    if repaired:
        status["derived_contexts_rebuild_required"] = True
        status["derived_contexts_rebuild_reason"] = "CURRENT.rules_version changed; rebuild through the existing state/query context producers"
    status["last_safe_repair_at"] = now.isoformat(timespec="seconds") if repaired else status.get("last_safe_repair_at")
    return status


def record_trigger(status: dict, now: datetime | None = None) -> dict:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    attempts = recent_attempts(status, now)
    attempts.append(now.isoformat(timespec="seconds"))
    status["snapshot_refresh_attempts"] = attempts
    status["last_snapshot_refresh_trigger_at"] = now.isoformat(timespec="seconds")
    status["trigger_recorded"] = True
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assess", action="store_true")
    parser.add_argument("--repair-safe", action="store_true")
    parser.add_argument("--record-trigger", action="store_true")
    args = parser.parse_args()

    status = assess()
    if args.repair_safe:
        status = repair_safe(status)
    if args.record_trigger:
        status = record_trigger(status)
    atomic_write_json(STATUS_PATH, status)
    print(json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
