from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "state"
DIAGNOSTIC = STATE / "workflow_failure_diagnostic.json"
ROLLBACK_PLAN = STATE / "restricted_rollback_plan.json"
TZ = timezone(timedelta(hours=8))

SUPPORTED_WORKFLOWS = {
    "ETF system consistency",
    "ETF runtime self-healing watchdog",
    "Overseas pre-open pulse",
    "US extended-hours pulse",
}

# Automatic rollback is deliberately limited to maintenance infrastructure.
# Formal trading rules, account facts, market/provider policy and market data are never eligible.
AUTO_ROLLBACK_ALLOWLIST = {
    "scripts/runtime_self_heal.py",
    "scripts/maintenance_guard.py",
    "scripts/workflow_failure_guard.py",
    ".github/workflows/self-healing-watchdog.yml",
    ".github/workflows/system-consistency.yml",
    ".github/workflows/workflow-failure-guard.yml",
}

FORBIDDEN_PREFIXES = (
    "ETF规则_MASTER.md",
    "ETF当前状态_DASHBOARD.md",
    "ETF交易复盘与经验库_2026.md",
    "ETF市场行情档案_2026.md",
    "data/state/account_fact.json",
    "events/trades/",
    "config/market/",
    "config/runtime_policy.json",
    "data/market/",
)

# These failures normally mean the workflow obtained facts successfully but did
# not persist them because Git/GitHub state moved concurrently. Retry once before
# escalating; an isolated persistence failure is not itself a user-facing system blockage.
TRANSIENT_STEP_HINTS = (
    "checkout",
    "persist",
    "commit",
    "push",
    "rebase",
    "setup-python",
)

DETERMINISTIC_STEP_HINTS = (
    "validate formal files",
    "reconcile etf ledger",
    "assess deterministic runtime health",
    "rebuild derived contexts safely",
    "repair deterministic metadata drift",
)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def normalize_files(value) -> list[str]:
    if isinstance(value, list):
        return sorted({str(x).strip() for x in value if str(x).strip()})
    return []


def failed_steps(payload: dict) -> list[dict]:
    result: list[dict] = []
    for job in payload.get("jobs") or []:
        for step in job.get("steps") or []:
            conclusion = str(step.get("conclusion") or "").lower()
            if conclusion == "failure":
                result.append({
                    "job": job.get("name"),
                    "step": step.get("name"),
                    "number": step.get("number"),
                })
    return result


def classify_failure(steps: list[dict]) -> str:
    names = " | ".join(str(x.get("step") or "").lower() for x in steps)
    if any(hint in names for hint in TRANSIENT_STEP_HINTS):
        return "WORKFLOW_TRANSIENT_OR_PERSISTENCE_FAILURE"
    if any(hint in names for hint in DETERMINISTIC_STEP_HINTS):
        return "DETERMINISTIC_MAINTENANCE_REGRESSION"
    return "WORKFLOW_FAILURE_UNCLASSIFIED"


def has_forbidden_path(files: list[str]) -> bool:
    for path in files:
        for prefix in FORBIDDEN_PREFIXES:
            if path == prefix or path.startswith(prefix):
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    args = parser.parse_args()

    payload = read_json(args.input, {}) or {}
    now = datetime.now(TZ).isoformat(timespec="seconds")
    workflow_name = str(payload.get("workflow_name") or "")
    run_id = payload.get("run_id")
    run_attempt = int(payload.get("run_attempt") or 1)
    head_sha = str(payload.get("head_sha") or "")
    main_head_sha = str(payload.get("main_head_sha") or "")
    commit_message = str(payload.get("commit_message") or "")
    commit_author = str(payload.get("commit_author") or "")
    files = normalize_files(payload.get("changed_files"))
    steps = failed_steps(payload)
    classification = classify_failure(steps)

    supported = workflow_name in SUPPORTED_WORKFLOWS
    head_is_current = bool(head_sha and main_head_sha and head_sha == main_head_sha)
    allowlisted_change = bool(files) and set(files).issubset(AUTO_ROLLBACK_ALLOWLIST)
    forbidden_change = has_forbidden_path(files)
    bot_or_revert_commit = (
        "github-actions" in commit_author.lower()
        or commit_message.lower().startswith("revert")
        or commit_message.lower().startswith("state:")
    )

    retry_eligible = (
        supported
        and run_attempt == 1
        and classification == "WORKFLOW_TRANSIENT_OR_PERSISTENCE_FAILURE"
    )

    rollback_eligible = (
        supported
        and classification == "DETERMINISTIC_MAINTENANCE_REGRESSION"
        and head_is_current
        and allowlisted_change
        and not forbidden_change
        and not bot_or_revert_commit
    )

    if retry_eligible:
        recommended_action = "RERUN_FAILED_JOBS_ONCE"
        reason = "failure is classified as transient/persistence and this is the first attempt"
    elif rollback_eligible:
        recommended_action = "AUTO_REVERT_HEAD_MAINTENANCE_COMMIT"
        reason = "deterministic regression is confined to the strict maintenance allowlist and failed SHA is still main HEAD"
    else:
        recommended_action = "ESCALATE_WITH_DIAGNOSTIC"
        reason = "automatic action stopped at safety boundary"

    diagnostic = {
        "schema_version": "1.0",
        "generated_at": now,
        "mode": "WORKFLOW_FAILURE_DIAGNOSIS",
        "workflow_name": workflow_name,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha or None,
        "main_head_sha": main_head_sha or None,
        "classification": classification,
        "failed_steps": steps,
        "changed_files": files,
        "commit_message": commit_message or None,
        "commit_author": commit_author or None,
        "recommended_action": recommended_action,
        "reason": reason,
        "safety": {
            "supported_workflow": supported,
            "head_is_current_main": head_is_current,
            "changes_confined_to_auto_rollback_allowlist": allowlisted_change,
            "forbidden_path_touched": forbidden_change,
            "bot_or_revert_commit": bot_or_revert_commit,
            "may_modify_master": False,
            "may_modify_account_fact": False,
            "may_change_provider_policy": False,
            "may_auto_trade": False,
            "may_ai_patch_main": False,
        },
    }
    write_json(DIAGNOSTIC, diagnostic)

    plan = {
        "schema_version": "1.0",
        "generated_at": now,
        "status": "AUTO_REVERT_ELIGIBLE" if rollback_eligible else "NOT_ELIGIBLE",
        "target_sha": head_sha if rollback_eligible else None,
        "main_head_sha_at_assessment": main_head_sha or None,
        "changed_files": files,
        "reason": reason,
        "guardrails": [
            "target SHA must still equal origin/main immediately before revert",
            "changed files must remain entirely inside strict maintenance allowlist",
            "never revert MASTER, trading rules, provider policy, market data or user-confirmed account facts",
            "never auto-revert github-actions state commits or a prior revert commit",
        ],
    }
    write_json(ROLLBACK_PLAN, plan)
    print(json.dumps(diagnostic, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
