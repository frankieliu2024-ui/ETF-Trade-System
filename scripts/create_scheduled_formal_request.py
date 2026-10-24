from __future__ import annotations

import json

import os
import secrets
import time
from urllib import request as urllib_request
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from formal_entry_contract import build_identity
    from task_runtime_identity import derive_runtime_identity
except ModuleNotFoundError:  # package import in repository tests
    from scripts.formal_entry_contract import build_identity
    from scripts.task_runtime_identity import derive_runtime_identity

ROOT = Path(__file__).resolve().parents[1]
REQUEST_DIR = ROOT / "requests" / "live_snapshot"
BEIJING = timezone(timedelta(hours=8))

# GitHub Actions cron is UTC; these are the five automatic Beijing nodes.
SLOTS = {
    "0 1 * * 1-5": (9, 0),
    "0 2 * * 1-5": (10, 0),
    "0 3 * * 1-5": (11, 0),
    "30 5 * * 1-5": (13, 30),
    "30 6 * * 1-5": (14, 30),
}

CONVERSATION_MODE = "MANUAL_CONVERSATION"
MANUAL_MODE = "MANUAL_SCHEDULED_TASK"
AUTOMATIC_MODE = "AUTOMATIC_SCHEDULED_TASK"
TRIGGER_SOURCES = {
    CONVERSATION_MODE: "CHATGPT_MANUAL_FORMAL_ANALYSIS",
    MANUAL_MODE: "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
    AUTOMATIC_MODE: "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
}


def classify_schedule_invocation(entry_mode: str, scheduled_cron: str) -> str:
    mode = str(entry_mode or "").strip().upper()
    if mode in {CONVERSATION_MODE, MANUAL_MODE}:
        return "RUN"
    if mode != AUTOMATIC_MODE:
        return "TERMINAL"
    return "RUN" if str(scheduled_cron or "").strip() in SLOTS else "SKIP"


def _manual_identity(now: datetime, entry_mode: str) -> tuple[str, str]:
    suffix = secrets.token_hex(4)
    value = now.strftime("%Y%m%dT%H%M%S%z")
    prefix = "conversation" if entry_mode == CONVERSATION_MODE else "manual-scheduled"
    run_id = f"{prefix}-{value}-{suffix}"
    return run_id, run_id


def build_scheduled_request(
    *,
    now: datetime,
    task_id: str,
    task_run_id: str,
    occurrence_id: str,
    entry_mode: str = AUTOMATIC_MODE,
) -> dict:
    mode = str(entry_mode or "").strip().upper()
    if mode not in TRIGGER_SOURCES:
        raise ValueError("unsupported scheduled task entry_mode")
    occurrence = str(occurrence_id or "").strip()
    if not occurrence:
        raise ValueError("occurrence_id is required")
    if not task_id or not task_run_id:
        raise ValueError("task_id and task_run_id are required")
    if mode == AUTOMATIC_MODE and not occurrence.startswith("scheduled_"):
        raise ValueError("automatic occurrence must start with scheduled_")
    if mode in {CONVERSATION_MODE, MANUAL_MODE} and occurrence.startswith("scheduled_"):
        raise ValueError("manual occurrence must not impersonate automatic occurrence")

    requested_at = now.astimezone(BEIJING).isoformat()
    request_id = (
        "fd_" + now.astimezone(BEIJING).strftime("%Y%m%d_%H%M%S")
        + "_scheduled_formal_review_" + secrets.token_hex(4)
    )
    trigger_source = TRIGGER_SOURCES[mode]
    identity = build_identity({
        "request_id": request_id,
        "source": trigger_source,
        "entry_mode": mode,
        "task_id": str(task_id),
        "task_run_id": str(task_run_id),
        "occurrence": occurrence,
        "market_date": now.astimezone(BEIJING).date().isoformat(),
    })
    return {
        "request_id": request_id,
        "request_type": "MARKET_QUOTE_REFRESH",
        "source": trigger_source,
        "entry_mode": mode,
        "intent": {
            "query_intent": "FORMAL_INTRADAY_ANALYSIS",
            "interaction_scenario": "FORMAL_DECISION",
        },
        "interaction_scenario": "FORMAL_DECISION",
        "force_refresh": True,
        "require_post_request_snapshot": True,
        "reuse_inflight_refresh": True,
        "duplicate_equivalent_refresh_forbidden": True,
        "requested_at_beijing": requested_at,
        "task_id": str(task_id),
        "task_run_id": str(task_run_id),
        "occurrence": identity.occurrence,
        "occurrence_id": identity.occurrence,
        "market_date": now.astimezone(BEIJING).date().isoformat(),
        "triggered_at_beijing": requested_at,
        "no_trade_authority": True,
        "identity_dedup_key": identity.dedup_key,
    }


\ndef persist_request_to_main(path: Path, payload: dict) -> dict[str, object]:
    """Persist the request on current main and prove the exact path is readable."""
    token = (os.environ.get("GITHUB_TOKEN", "").strip()
             or os.environ.get("GH_TOKEN", "").strip())
    repository = (os.environ.get("GITHUB_REPOSITORY", "").strip()
                  or os.environ.get("ETF_TRADE_SYSTEM_REPOSITORY", "").strip()
                  or "frankieliu2024-ui/ETF-Trade-System")
    relative = str(path.relative_to(ROOT))
    if not token:
        return {"written": False, "reason": "missing GitHub Contents token", "path": relative}
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        return {"written": False, "reason": "invalid repository", "path": relative}
    base = f"https://api.github.com/repos/{owner}/{repo}"
    endpoint = f"{base}/contents/{relative}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    encoded = __import__("base64").b64encode(body.encode("utf-8")).decode("ascii")
    request_body = json.dumps({
        "message": f"ETF formal decision request: {payload['request_id']}",
        "content": encoded,
        "branch": "main",
    }).encode("utf-8")
    try:
        req = urllib_request.Request(endpoint, data=request_body, method="PUT", headers=headers)
        with urllib_request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        commit_sha = str((result.get("commit") or {}).get("sha") or "")
        blob_sha = str((result.get("content") or {}).get("sha") or "")
        if not commit_sha or not blob_sha:
            return {"written": False, "reason": "Contents write returned no commit/blob SHA", "path": relative}
        read_req = urllib_request.Request(endpoint + "?ref=main", method="GET", headers=headers)
        with urllib_request.urlopen(read_req, timeout=30) as response:
            readback = json.loads(response.read().decode("utf-8"))
        read_content = __import__("base64").b64decode(
            str(readback.get("content") or "").replace("\n", "")
        ).decode("utf-8")
        expected = json.loads(body)
        actual = json.loads(read_content)
        identity_keys = (
            "request_id", "task_id", "task_run_id", "occurrence_id",
            "market_date", "requested_at_beijing", "source", "entry_mode",
        )
        if any(actual.get(key) != expected.get(key) for key in identity_keys):
            return {"written": False, "reason": "same-path current-main readback identity mismatch",
                    "path": relative, "commit_sha": commit_sha, "blob_sha": blob_sha}
        return {"written": True, "path": relative, "commit_sha": commit_sha,
                "blob_sha": blob_sha, "read_back": True}
    except Exception as exc:
        return {"written": False, "reason": str(exc), "path": relative}

def dispatch_market_snapshot(request_path: Path, commit_sha: str) -> dict[str, object]:
    """Explicitly dispatch one snapshot run after the request is durable on main."""
    token = (os.environ.get("GITHUB_TOKEN", "").strip()
             or os.environ.get("GH_TOKEN", "").strip())
    repository = (os.environ.get("GITHUB_REPOSITORY", "").strip()
                  or os.environ.get("ETF_TRADE_SYSTEM_REPOSITORY", "").strip()
                  or "frankieliu2024-ui/ETF-Trade-System")
    request_relpath = str(request_path.relative_to(ROOT))
    if not token:
        return {"submitted": False, "reason": "missing GitHub Actions token (GITHUB_TOKEN or GH_TOKEN)",
                "request_path": request_relpath, "commit_sha": commit_sha}
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        return {"submitted": False, "reason": "invalid GITHUB_REPOSITORY"}
    base = f"https://api.github.com/repos/{owner}/{repo}"
    dispatch_endpoint = f"{base}/actions/workflows/market-snapshot.yml/dispatches"
    runs_endpoint = (
        f"{base}/actions/workflows/market-snapshot.yml/runs"
        "?event=workflow_dispatch&branch=main&per_page=20"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    dispatch_body = json.dumps({
        "ref": "main",
        "inputs": {
            "node": "scheduled",
            "probe_only": "false",
            "formal_context_recovery_request": request_relpath,
        },
    }).encode("utf-8")
    dispatched_at = datetime.now(timezone.utc)
    try:
        req = urllib_request.Request(
            dispatch_endpoint, data=dispatch_body, method="POST", headers=headers
        )
        with urllib_request.urlopen(req, timeout=30) as response:
            if response.status != 204:
                return {"submitted": False, "status": response.status,
                        "request_path": request_relpath, "commit_sha": commit_sha}
    except Exception as exc:
        return {"submitted": False, "reason": str(exc),
                "request_path": request_relpath, "commit_sha": commit_sha}
    deadline = datetime.now(timezone.utc) + timedelta(seconds=90)
    while datetime.now(timezone.utc) < deadline:
        try:
            req = urllib_request.Request(runs_endpoint, method="GET", headers=headers)
            with urllib_request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            for run in data.get("workflow_runs", []):
                created_at = str(run.get("created_at") or "")
                if not created_at:
                    continue
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if (
                    created >= dispatched_at
                    and str(run.get("event") or "") == "workflow_dispatch"
                    and str(run.get("head_branch") or "") == "main"
                ):
                    return {
                        "submitted": True, "trigger": "workflow_dispatch",
                        "run_id": run.get("id"), "run_url": run.get("html_url"),
                        "run_status": run.get("status"),
                        "run_conclusion": run.get("conclusion"),
                        "request_path": request_relpath,
                        "commit_sha": commit_sha, "read_back": True,
                    }
        except Exception:
            pass
        time.sleep(3)
    return {
        "submitted": False,
        "reason": "workflow_dispatch accepted but matching run was not read back",
        "request_path": request_relpath, "commit_sha": commit_sha, "read_back": False,
    }

def main() -> int:
    mode = os.environ.get("ENTRY_MODE", AUTOMATIC_MODE).strip().upper()
    cron = os.environ.get("SCHEDULED_CRON", "").strip()
    decision = classify_schedule_invocation(mode, cron)
    if decision == "SKIP":
        print("FORMAL_SCHEDULE_INGRESS=SKIP")
        return 0
    if decision == "TERMINAL":
        raise SystemExit("FORMAL_SCHEDULE_INGRESS=INVALID_ENTRY_MODE")

    now = datetime.now(BEIJING)
    task_id = os.environ.get("TASK_ID", "").strip() or "ETF正式决策"
    task_run_id = os.environ.get("TASK_RUN_ID", "").strip()
    occurrence = os.environ.get("OCCURRENCE_ID", "").strip()

    if mode in {CONVERSATION_MODE, MANUAL_MODE}:
        if not task_run_id or not occurrence:
            task_run_id, occurrence = _manual_identity(now, mode)
    else:
        if cron not in SLOTS:
            raise SystemExit("FORMAL_SCHEDULE_INGRESS=INVALID_AUTOMATIC_SLOT")
        task_run_id, occurrence = derive_runtime_identity(
            task_id, "scheduled-formal-entry", now.astimezone(BEIJING).isoformat(),
            mode, task_run_id, occurrence
        )

    # Every legal invocation is a new ingress. Previous gate, READY request,
    # completion, or failed projection must not replace this run's identity.

    payload = build_scheduled_request(
        now=now,
        task_id=task_id,
        task_run_id=task_run_id,
        occurrence_id=occurrence,
        entry_mode=mode,
    )
    path = REQUEST_DIR / f"{payload['request_id']}.json"
    persisted = persist_request_to_main(path, payload)
    if not persisted.get("written"):
        raise SystemExit(
            "FORMAL_SCHEDULE_INGRESS=REQUEST_PERSIST_FAILED "
            + json.dumps(persisted, ensure_ascii=False)
        )
    dispatch = dispatch_market_snapshot(path, str(persisted.get("commit_sha") or ""))
    if not dispatch.get("submitted"):
        raise SystemExit(
            "FORMAL_SCHEDULE_INGRESS=SNAPSHOT_DISPATCH_FAILED "
            + json.dumps(dispatch, ensure_ascii=False)
        )
    print(json.dumps({
        "status": "CREATED",
        "request_id": payload["request_id"],
        "path": str(path),
        "request_persistence": persisted,
        "market_snapshot_dispatch": dispatch,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
