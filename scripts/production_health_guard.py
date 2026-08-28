from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
POLICY = ROOT / "config" / "runtime_policy.json"
CONSISTENCY_WORKFLOW = ROOT / ".github" / "workflows" / "system-consistency.yml"
SYSTEM_INDEX = ROOT / "ETF_SYSTEM_INDEX.md"
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


def normative_rule_paths() -> list[str]:
    """Read the four normative domain paths from the canonical system index."""
    try:
        lines = SYSTEM_INDEX.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    paths: list[str] = []
    for line in lines:
        if not line.startswith("- **") or "唯一" not in line:
            continue
        pieces = line.split("`")
        for value in pieces[1::2]:
            value = value.strip()
            if value.endswith(".md") and value not in paths:
                paths.append(value)
                break
    return paths


def consistency_push_patterns() -> list[str]:
    """Read canonical revalidation semantics without maintaining a second path list.

    Machine trigger paths come from the full-consistency workflow. Normative rule
    sources additionally come from ETF_SYSTEM_INDEX.md so a rule-only change can
    never be mistaken for harmless state advancement.
    """
    try:
        lines = CONSISTENCY_WORKFLOW.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    in_push = False
    in_paths = False
    patterns: list[str] = []
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2 and stripped == "push:":
            in_push = True
            in_paths = False
            continue
        if in_push and indent == 2 and stripped and stripped != "push:":
            break
        if not in_push:
            continue
        if indent == 4 and stripped == "paths:":
            in_paths = True
            continue
        if not in_paths:
            continue
        if indent == 6 and stripped.startswith("- "):
            value = stripped[2:].strip().strip('"').strip("'")
            if value and value not in patterns:
                patterns.append(value)
            continue
        if stripped and not stripped.startswith("#") and indent <= 4:
            in_paths = False

    for path in normative_rule_paths():
        if path not in patterns:
            patterns.append(path)
    return patterns


def current_head() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def changed_files_since(base: str, head: str) -> tuple[list[str] | None, str]:
    """Return changed paths, preferring local git and falling back to one GitHub compare call.

    Watchdog checkout is intentionally shallow. The API fallback avoids increasing every
    watchdog checkout to full history merely to validate coverage.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"{base}..{head}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode == 0:
            return [x.strip() for x in proc.stdout.splitlines() if x.strip()], "LOCAL_GIT"
    except (OSError, subprocess.SubprocessError):
        pass

    repo = str(os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not repo:
        return None, "UNVERIFIABLE_NO_REPOSITORY"
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo}/compare/{base}...{head}", "--jq", ".files[].filename"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "UNVERIFIABLE_COMPARE_ERROR"
    if proc.returncode != 0:
        return None, "UNVERIFIABLE_COMPARE_ERROR"
    return [x.strip() for x in proc.stdout.splitlines() if x.strip()], "GITHUB_COMPARE"


def classify_consistency_coverage(
    consistency_status: str,
    checked_commit: str | None,
    head: str | None,
    changed_files: list[str] | None,
    patterns: list[str],
) -> tuple[str, str, list[str]]:
    """Classify whether the latest consistency result still covers current production definition."""
    if consistency_status == "FAIL":
        return "BLOCKED", "CONSISTENCY_FAIL", []
    if consistency_status != "PASS":
        return "ATTENTION", "CONSISTENCY_NOT_PASS", []
    if not checked_commit or not head:
        return "ATTENTION", "COVERAGE_UNVERIFIABLE", []
    if checked_commit == head:
        return "PASS", "CURRENT_HEAD", []
    if changed_files is None or not patterns:
        return "ATTENTION", "COVERAGE_UNVERIFIABLE", []

    triggering = sorted(
        path for path in changed_files if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
    )
    if triggering:
        return "ATTENTION", "REVALIDATION_REQUIRED", triggering
    return "PASS", "STATE_ONLY_ADVANCE", []


def consistency_coverage(consistency: dict[str, Any]) -> tuple[str, str]:
    status = str(consistency.get("status") or "UNKNOWN")
    checked_commit = str(
        ((consistency.get("commit_audit") or {}).get("checked_commit"))
        or ((consistency.get("repository") or {}).get("head_sha"))
        or ""
    ).strip()
    head = current_head()
    changed: list[str] | None = [] if checked_commit and head and checked_commit == head else None
    source = "NOT_NEEDED"
    if checked_commit and head and checked_commit != head:
        changed, source = changed_files_since(checked_commit, head)
    patterns = consistency_push_patterns()
    level, coverage, triggering = classify_consistency_coverage(status, checked_commit, head, changed, patterns)
    detail = (
        f"status={status}; coverage={coverage}; checked_commit={checked_commit or 'unknown'}; "
        f"current_head={head or 'unknown'}; compare_source={source}; changed_count="
        f"{len(changed) if changed is not None else 'unknown'}"
    )
    if triggering:
        preview = ",".join(triggering[:5])
        detail += f"; revalidation_paths={preview}"
        if len(triggering) > 5:
            detail += f"(+{len(triggering) - 5})"
    return level, detail


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

    consistency_level, consistency_detail = consistency_coverage(consistency)
    add_check(checks, "full_system_consistency", consistency_level, consistency_detail)

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
    add_check(checks, "maintenance_and_account_reconciliation", "BLOCKED" if maintenance_block else ("PASS" if maintenance_status == "PASS" and reconciliation_status == "PASS" else "ATTENTION"), f"maintenance={maintenance_status}; reconciliation={reconciliation_status}")

    account_status = str(account.get("status") or "UNKNOWN")
    actionable_count = int(execution.get("actionable_count") or 0)
    execution_status = str(execution.get("status") or "UNKNOWN")
    account_action = actionable_count > 0 or execution_status == "CONFIRMATION_REQUIRED"
    add_check(checks, "account_and_execution_closure", "ATTENTION" if account_action else ("PASS" if account_status == "VALID" else "ATTENTION"), f"account={account_status}; execution={execution_status}; actionable_count={actionable_count}", user_action=account_action)

    e2e_status = str(e2e.get("status") or "UNKNOWN")
    blockers = list(e2e.get("blockers") or [])
    add_check(checks, "end_to_end_readiness", "PASS" if e2e_status == "READY" else ("BLOCKED" if e2e_status == "BLOCKED" else "ATTENTION"), f"status={e2e_status}; blockers={blockers}")

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
        "schema_version": "1.1",
        "mode": "READ_ONLY_PRODUCTION_HEALTH_AGGREGATE",
        "checked_at": now_bj.isoformat(timespec="seconds"),
        "status": overall,
        "user_action_required": user_action_required,
        "market_activity": windows,
        "checks": checks,
        "notification_boundary": "This guard never sends PushPlus directly. User-action notifications continue through the existing guarded notification center triggered after the watchdog run. Consistency/maintenance/E2E anomalies do not by themselves gain notification rights before existing self-healing escalation rules are satisfied.",
        "mutation_boundary": "Read-only: no market refresh, no workflow dispatch, no state persistence, no MASTER/provider/trading-rule/account-fact change.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
