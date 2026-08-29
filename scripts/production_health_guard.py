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
    """Read normative domain paths from section 2 of the canonical system index.

    The index is deliberately route-only. Authority is expressed by membership in
    the four normative-domain bullets, not by requiring one historical wording
    such as the word "唯一" to be repeated on every bullet. This keeps the health
    guard bound to structure/semantics rather than prose placement.
    """
    try:
        lines = SYSTEM_INDEX.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    paths: list[str] = []
    in_domains = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## 2. 四个规范域":
            in_domains = True
            continue
        if in_domains and stripped.startswith("## "):
            break
        if not in_domains or not stripped.startswith("- **"):
            continue
        pieces = stripped.split("`")
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
        return None, "UNVERIFIABLE_COMPARE_FAILED"
    return [x.strip() for x in proc.stdout.splitlines() if x.strip()], "GITHUB_COMPARE"


def path_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern)


def classify_consistency_coverage(
    consistency_status: str,
    checked_commit: str | None,
    current_commit: str | None,
    changed_paths: list[str] | None,
    trigger_patterns: list[str],
) -> tuple[str, str, list[str]]:
    if str(consistency_status).upper() == "FAIL":
        return "BLOCKED", "CONSISTENCY_FAIL", []
    if not checked_commit or not current_commit:
        return "ATTENTION", "COMMIT_IDENTITY_UNAVAILABLE", []
    if checked_commit == current_commit:
        return "PASS", "CURRENT_HEAD", []
    if changed_paths is None:
        return "ATTENTION", "CHANGESET_UNVERIFIABLE", []
    relevant = sorted(
        path
        for path in changed_paths
        if any(path_matches(path, pattern) for pattern in trigger_patterns)
    )
    if relevant:
        return "ATTENTION", "REVALIDATION_REQUIRED", relevant
    return "PASS", "STATE_ONLY_ADVANCE", []


def main() -> int:
    now_utc = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    consistency = read_json(STATE / "system_consistency.json")
    e2e = read_json(STATE / "e2e_status.json")
    current = read_json(STATE / "CURRENT.json")
    runtime = read_json(STATE / "runtime_health.json")
    overseas = read_json(STATE / "overseas_context.json")
    overseas_health = read_json(STATE / "overseas_runtime_health.json")
    us = read_json(STATE / "us_extended_hours_context.json")
    us_health = read_json(STATE / "us_pulse_runtime_health.json")
    account = read_json(STATE / "account_fact.json")
    notification = read_json(STATE / "notification_center.json")
    workflow_diag = read_json(STATE / "workflow_failure_diagnostic.json")
    windows = active_windows(now_utc)

    consistency_status = str(consistency.get("status") or "UNKNOWN").upper()
    add_check(rows, "system_consistency", "PASS" if consistency_status == "PASS" else "BLOCKED" if consistency_status == "FAIL" else "ATTENTION", f"status={consistency_status}")

    checked_commit = str(((consistency.get("commit_audit") or {}).get("checked_commit") or "")).strip() or None
    head = current_head()
    changed: list[str] | None = []
    change_source = "CURRENT_HEAD"
    if checked_commit and head and checked_commit != head:
        changed, change_source = changed_files_since(checked_commit, head)
    level, coverage, relevant_paths = classify_consistency_coverage(
        consistency_status,
        checked_commit,
        head,
        changed,
        consistency_push_patterns(),
    )
    add_check(
        rows,
        "consistency_coverage",
        level,
        f"coverage={coverage} checked_commit={checked_commit} current_head={head} change_source={change_source} relevant_paths={relevant_paths}",
    )

    current_status = str(current.get("snapshot_status") or current.get("node_status") or "UNKNOWN").upper()
    add_check(rows, "a_share_current", "PASS" if current_status in {"PASS", "READY"} else "BLOCKED", f"status={current_status} snapshot={current.get('latest_snapshot')}")

    runtime_status = str(runtime.get("status") or "UNKNOWN").upper()
    runtime_ok = runtime_status in {"PASS", "SKIPPED"}
    add_check(rows, "a_share_runtime", "PASS" if runtime_ok else "BLOCKED", f"status={runtime_status} reason={runtime.get('reason')}")

    overseas_status = str(overseas.get("quality_status") or "UNKNOWN").upper()
    add_check(rows, "overseas_context", "PASS" if overseas_status in {"PASS", "DEGRADED"} else "ATTENTION", f"quality_status={overseas_status}")

    us_status = str(us.get("quality_status") or "UNKNOWN").upper()
    add_check(rows, "us_extended_hours", "PASS" if us_status in {"PASS", "DEGRADED"} else "ATTENTION", f"quality_status={us_status}")

    account_status = str(account.get("status") or "UNKNOWN").upper()
    add_check(rows, "account_fact", "PASS" if account_status == "VALID" else "ATTENTION", f"status={account_status}", user_action=account_status != "VALID")

    for name, health, active in (
        ("a_share", runtime, windows["a_share"]),
        ("apac", overseas_health, windows["apac"]),
        ("us", us_health, windows["us"]),
    ):
        status = str(health.get("status") or "UNKNOWN").upper()
        stamp = health.get("generated_at_beijing") or health.get("updated_at_beijing") or health.get("generated_at")
        age = age_seconds(now_utc, stamp)
        if active and status in {"FAILED", "BLOCKED"}:
            level = "BLOCKED"
        elif active and (age is None or age > 1800):
            level = "ATTENTION"
        else:
            level = "PASS"
        add_check(rows, f"heartbeat:{name}", level, f"active={active} status={status} age_seconds={age}")

    user_event = current_user_action_event()
    add_check(rows, "pending_user_action", "ATTENTION" if user_event else "PASS", f"event={user_event.get('title') if user_event else None}", user_action=bool(user_event))

    diagnostic_status = str(workflow_diag.get("classification") or "NONE")
    add_check(rows, "workflow_diagnostic", "ATTENTION" if diagnostic_status not in {"", "NONE", "NO_FAILURE"} else "PASS", f"classification={diagnostic_status}")

    overall = "BLOCKED" if any(row["status"] == "BLOCKED" for row in rows) else "ATTENTION" if any(row["status"] == "ATTENTION" for row in rows) else "PASS"
    payload = {
        "schema_version": "1.0",
        "generated_at_beijing": now_utc.astimezone(BJ).isoformat(timespec="seconds"),
        "status": overall,
        "checks": rows,
        "notification_center_count": len(notification.get("managed_events") or []),
        "rule": "生产健康只读检查；不生成交易权限，不修改MASTER，不自动下单。",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2 if overall == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
