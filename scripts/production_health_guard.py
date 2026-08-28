from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
POLICY = ROOT / "config" / "runtime_policy.json"
BJ = ZoneInfo("Asia/Shanghai")
ET = ZoneInfo("America/New_York")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_seconds(now_utc: datetime, value: Any) -> int | None:
    dt = parse_time(value)
    return max(0, int((now_utc - dt).total_seconds())) if dt else None


def add_check(rows: list[dict[str, Any]], name: str, status: str, detail: str, *, user_action: bool = False) -> None:
    rows.append({"name": name, "status": status, "detail": detail, "user_action_required": user_action})


def active_windows(now_utc: datetime) -> dict[str, bool]:
    now_bj = now_utc.astimezone(BJ)
    now_et = now_utc.astimezone(ET)
    bj_min = now_bj.hour * 60 + now_bj.minute
    et_min = now_et.hour * 60 + now_et.minute
    return {
        "a_share": now_bj.weekday() < 5 and ((9 * 60 + 15) <= bj_min <= (11 * 60 + 30) or (13 * 60) <= bj_min <= (15 * 60 + 30)),
        "apac": now_bj.weekday() < 5 and 8 * 60 <= bj_min < 16 * 60,
        "us": now_et.weekday() < 5 and 4 * 60 <= et_min <= 20 * 60,
    }


def current_user_action_event() -> dict[str, Any] | None:
    try:
        import notification_center

        event = notification_center.choose_event("event")
        return event if isinstance(event, dict) else None
    except Exception:
        return None


def main() -> int:
    now_utc = datetime.now(timezone.utc)
    now_bj = now_utc.astimezone(BJ)
    windows = active_windows(now_utc)
    policy = read_json(POLICY)
    degraded_max_age = int(policy.get("degraded_max_age_seconds") or 1500)

    consistency = read_json(STATE / "system_consistency.json")
    runtime = read_json(STATE / "runtime_health.json")
    self_heal = read_json(STATE / "self_healing_status.json")
    overseas_runtime = read_json(STATE / "overseas_runtime_health.json")
    us_runtime = read_json(STATE / "us_pulse_runtime_health.json")
    maintenance = read_json(STATE / "maintenance_health.json")
    e2e = read_json(STATE / "e2e_status.json")
    account = read_json(STATE / "account_fact.json")
    execution = read_json(STATE / "execution_reconciliation.json")

    checks: list[dict[str, Any]] = []

    consistency_status = str(consistency.get("status") or "UNKNOWN")
    add_check(
        checks,
        "full_system_consistency",
        "PASS" if consistency_status == "PASS" else "BLOCKED",
        f"status={consistency_status}; generated_at={consistency.get('generated_at_beijing') or consistency.get('generated_at') or 'unknown'}",
        user_action=consistency_status == "FAIL",
    )

    runtime_status = str(runtime.get("status") or runtime.get("quality_status") or "UNKNOWN")
    runtime_age = age_seconds(now_utc, runtime.get("finished_at") or runtime.get("captured_at") or runtime.get("generated_at"))
    if windows["a_share"]:
        a_status = "PASS" if runtime_status == "PASS" and runtime_age is not None and runtime_age <= degraded_max_age else "ATTENTION"
        add_check(checks, "a_share_market_runtime", a_status, f"active=true; status={runtime_status}; heartbeat_age_seconds={runtime_age}")
    else:
        add_check(checks, "a_share_market_runtime", "PASS" if runtime_status == "PASS" else "ATTENTION", f"active=false; last_runtime_status={runtime_status}; closed/session reference is not judged by live-heartbeat age")

    overseas_status = str(overseas_runtime.get("status") or "UNKNOWN")
    overseas_age = age_seconds(now_utc, overseas_runtime.get("finished_at"))
    if windows["apac"]:
        apac_status = "PASS" if overseas_status == "PASS" and overseas_age is not None and overseas_age <= degraded_max_age else "ATTENTION"
        add_check(checks, "apac_workflow_and_market_heartbeat", apac_status, f"active=true; status={overseas_status}; heartbeat_age_seconds={overseas_age}")
    else:
        add_check(checks, "apac_workflow_and_market_heartbeat", "PASS" if overseas_status == "PASS" else "ATTENTION", f"active=false; last_status={overseas_status}; closed markets retain session-reference semantics")

    us_status = str(us_runtime.get("status") or "UNKNOWN")
    us_age = age_seconds(now_utc, us_runtime.get("finished_at"))
    if windows["us"]:
        us_check = "PASS" if us_status == "PASS" and us_age is not None and us_age <= degraded_max_age else "ATTENTION"
        add_check(checks, "us_workflow_and_extended_hours_heartbeat", us_check, f"active=true; status={us_status}; heartbeat_age_seconds={us_age}")
    else:
        add_check(checks, "us_workflow_and_extended_hours_heartbeat", "PASS" if us_status == "PASS" else "ATTENTION", f"active=false; last_status={us_status}; no live-heartbeat requirement outside US extended-hours window")

    heal_class = str(self_heal.get("classification") or "UNKNOWN")
    heal_action = str(self_heal.get("recommended_action") or "NONE")
    heal_block = heal_action == "ESCALATE" or heal_class in {"PERSISTENT_RUNTIME_FAILURE", "CONSISTENCY_REGRESSION"}
    add_check(checks, "runtime_self_healing", "BLOCKED" if heal_block else "PASS", f"classification={heal_class}; recommended_action={heal_action}", user_action=heal_block)

    maintenance_status = str(maintenance.get("status") or "UNKNOWN")
    reconciliation_status = str((maintenance.get("reconciliation") or {}).get("status") or "UNKNOWN")
    maintenance_block = maintenance_status == "FAIL" or reconciliation_status == "FAIL"
    add_check(checks, "maintenance_and_account_reconciliation", "BLOCKED" if maintenance_block else ("PASS" if maintenance_status == "PASS" and reconciliation_status == "PASS" else "ATTENTION"), f"maintenance={maintenance_status}; reconciliation={reconciliation_status}", user_action=maintenance_block)

    account_status = str(account.get("status") or "UNKNOWN")
    actionable_count = int(execution.get("actionable_count") or 0)
    execution_status = str(execution.get("status") or "UNKNOWN")
    account_action = actionable_count > 0 or execution_status == "CONFIRMATION_REQUIRED"
    add_check(checks, "account_and_execution_closure", "ATTENTION" if account_action else ("PASS" if account_status == "VALID" else "ATTENTION"), f"account={account_status}; execution={execution_status}; actionable_count={actionable_count}", user_action=account_action)

    e2e_status = str(e2e.get("status") or "UNKNOWN")
    blockers = list(e2e.get("blockers") or [])
    add_check(checks, "end_to_end_readiness", "PASS" if e2e_status == "READY" else ("BLOCKED" if e2e_status == "BLOCKED" else "ATTENTION"), f"status={e2e_status}; blockers={blockers}", user_action=e2e_status == "BLOCKED")

    event = current_user_action_event()
    if event:
        add_check(checks, "existing_pushplus_user_action_path", "ATTENTION", f"event_type={event.get('event_type') or event.get('type')}; source={event.get('source')}; title={event.get('title')}", user_action=True)
    else:
        add_check(checks, "existing_pushplus_user_action_path", "PASS", "no current event requires the existing guarded PushPlus user-action path")

    user_action_required = any(bool(row.get("user_action_required")) for row in checks)
    blocked = any(row.get("status") == "BLOCKED" for row in checks)
    attention = any(row.get("status") == "ATTENTION" for row in checks)
    overall = "BLOCKED" if blocked else ("ATTENTION" if attention else "PASS")

    result = {
        "schema_version": "1.0",
        "mode": "READ_ONLY_PRODUCTION_HEALTH_AGGREGATE",
        "checked_at": now_bj.isoformat(timespec="seconds"),
        "status": overall,
        "user_action_required": user_action_required,
        "market_activity": windows,
        "checks": checks,
        "notification_boundary": "This guard never sends PushPlus directly. User-action notifications continue through the existing guarded notification center triggered after the watchdog run.",
        "mutation_boundary": "Read-only: no market refresh, no workflow dispatch, no state persistence, no MASTER/provider/trading-rule/account-fact change.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
