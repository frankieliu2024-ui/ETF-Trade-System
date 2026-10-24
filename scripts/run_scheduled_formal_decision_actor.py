"""Run the shared Scheduled Review Actor handoff for Formal Decision.

The actor freezes one user-visible investment judgment, delegates persistence
to the same completion owner used by Trade Review, and explicitly wakes the
existing notification workflow after completion readback. It does not create
a BDS, publish an event, or execute a trade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request as urllib_request

try:
    from scheduled_review_completion import write_completion_request
except ModuleNotFoundError:
    from scripts.scheduled_review_completion import write_completion_request

try:
    from task_runtime_identity import derive_runtime_identity
except ModuleNotFoundError:
    from scripts.task_runtime_identity import derive_runtime_identity


def _dispatch_notification(completion_path: str) -> dict[str, object]:
    """Dispatch notification and read back the matching workflow run."""
    token = (os.environ.get("GITHUB_TOKEN", "").strip()
             or os.environ.get("GH_TOKEN", "").strip())
    repository = (os.environ.get("GITHUB_REPOSITORY", "").strip()
                  or "frankieliu2024-ui/ETF-Trade-System")
    if not token:
        return {"submitted": False, "reason": "missing GitHub API token (GITHUB_TOKEN or GH_TOKEN)"}
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        return {"submitted": False, "reason": "invalid GITHUB_REPOSITORY"}

    payload = json.dumps({
        "ref": "main",
        # Use the same canonical input as System Review and Trade Review.
        # Keep formal_completion_path as a compatibility alias in the workflow,
        # but make the shared Scheduled Review path the primary binding.
        "inputs": {"scheduled_review_completion_path": completion_path},
    }).encode("utf-8")
    base = f"https://api.github.com/repos/{owner}/{repo}"
    dispatch_endpoint = f"{base}/actions/workflows/decision-notification.yml/dispatches"
    runs_endpoint = (
        f"{base}/actions/workflows/decision-notification.yml/runs"
        "?event=workflow_dispatch&branch=main&per_page=20"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    dispatched_at = datetime.now(timezone.utc)

    try:
        req = urllib_request.Request(
            dispatch_endpoint, data=payload, method="POST", headers=headers
        )
        with urllib_request.urlopen(req, timeout=30) as response:
            if response.status != 204:
                return {"submitted": False, "status": response.status}
    except Exception as exc:
        return {"submitted": False, "reason": str(exc)}

    deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
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
                        "submitted": True,
                        "status": 204,
                        "run_id": run.get("id"),
                        "run_url": run.get("html_url"),
                        "run_status": run.get("status"),
                        "run_conclusion": run.get("conclusion"),
                        "completion_path": completion_path,
                        "read_back": True,
                    }
        except Exception:
            pass
        time.sleep(3)

    return {
        "submitted": False,
        "reason": "notification workflow dispatch accepted but matching run was not read back",
        "completion_path": completion_path,
        "read_back": False,
    }



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-review", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--requested-at-beijing", required=True)
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--parent-request-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--final-content", required=True)
    parser.add_argument("--task-run-id", default="")
    parser.add_argument("--occurrence-id", default="")
    parser.add_argument("--entry-mode", required=True, choices=(
        "MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"
    ))
    args = parser.parse_args()

    task_run_id, occurrence_id = derive_runtime_identity(
        "ETF正式决策", args.request_id, args.requested_at_beijing,
        args.entry_mode, args.task_run_id, args.occurrence_id,
    )
    target = write_completion_request(
        args.formal_review, args.request_id, args.requested_at_beijing,
        args.market_date, args.parent_request_id, args.output,
        args.final_content, "ETF正式决策", task_run_id, occurrence_id,
        args.entry_mode,
    )
    persisted = json.loads(Path(target).read_text(encoding="utf-8"))
    binding = persisted.get("presentation_binding") or {}
    expected_hash = hashlib.sha256(args.final_content.encode("utf-8")).hexdigest()
    checks = {
        "request_id": persisted.get("request_id") == args.request_id,
        "task_id": persisted.get("task_id") == "ETF正式决策",
        "task_run_id": persisted.get("task_run_id") == task_run_id,
        "occurrence_id": persisted.get("occurrence_id") == occurrence_id,
        "entry_mode": persisted.get("entry_mode") == args.entry_mode,
        "request_type": persisted.get("request_type") == "SCHEDULED_REVIEW_COMPLETION",
        "no_bds_path": "bds_path" not in persisted,
        "report_type": binding.get("report_type") == "ETF_FORMAL_DECISION",
        "content_hash": binding.get("content_hash") == expected_hash,
        "full_content": binding.get("full_content") == args.final_content,
    }
    if not all(checks.values()):
        failed = ", ".join(key for key, ok in checks.items() if not ok)
        raise RuntimeError(f"formal decision completion readback mismatch: {failed}")

    dispatch = _dispatch_notification(str(target))
    print(json.dumps({
        "ok": True,
        "completion_path": str(target),
        "readback": checks,
        "notification_dispatch": dispatch,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
