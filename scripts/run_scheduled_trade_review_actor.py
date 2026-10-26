"""Run the canonical Trade Review Actor handoff.

This is the single production adapter between a completed Trade Review run and
the existing Scheduled Review completion builder. It does not build a second
payload, publish events, send notifications, or re-analyse the review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urllib_request

try:
    from scheduled_review_completion import write_completion_request
    from task_runtime_identity import derive_runtime_identity
except ModuleNotFoundError:
    from scripts.scheduled_review_completion import write_completion_request
    from scripts.task_runtime_identity import derive_runtime_identity

try:
    from scheduled_review_completion_recovery import persist_completion_with_recovery
except ModuleNotFoundError:
    from scripts.scheduled_review_completion_recovery import persist_completion_with_recovery



def _persist_completion_to_main(completion_path: str) -> str:
    """Persist and verify the single completion before dispatching notification."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise RuntimeError("cannot persist trade review completion without GITHUB_TOKEN and GITHUB_REPOSITORY")
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        raise RuntimeError("invalid GITHUB_REPOSITORY")
    path = Path(completion_path).resolve()
    try:
        relative_path = path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
    except ValueError as exc:
        raise RuntimeError("trade review completion must be inside the repository root") from exc
    body = path.read_bytes()
    base = "https://api.github.com/repos/" + owner + "/" + repo
    endpoint = base + "/contents/" + relative_path
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        read_req = urllib_request.Request(endpoint + "?ref=main", method="GET", headers=headers)
        with urllib_request.urlopen(read_req, timeout=30) as response:
            existing = json.loads(response.read().decode("utf-8"))
        existing_body = __import__("base64").b64decode(str(existing.get("content") or "").replace("\\n", ""))
        if existing_body != body:
            raise RuntimeError("different completion already exists on main: " + relative_path)
        return relative_path
    except Exception as exc:
        if getattr(exc, "code", None) != 404:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError("failed to read existing completion on main: " + str(exc)) from exc
    payload = json.dumps({
        "message": "Persist Scheduled Review completion",
        "content": __import__("base64").b64encode(body).decode("ascii"),
        "branch": "main",
    }).encode("utf-8")
    write_req = urllib_request.Request(endpoint, data=payload, method="PUT", headers=headers)
    try:
        with urllib_request.urlopen(write_req, timeout=30) as response:
            if response.status not in (200, 201):
                raise RuntimeError("completion persistence returned HTTP " + str(response.status))
    except Exception as exc:
        raise RuntimeError("failed to persist trade review completion on main: " + str(exc)) from exc
    verify_req = urllib_request.Request(endpoint + "?ref=main", method="GET", headers=headers)
    try:
        with urllib_request.urlopen(verify_req, timeout=30) as response:
            verified = json.loads(response.read().decode("utf-8"))
        verified_body = __import__("base64").b64decode(str(verified.get("content") or "").replace("\\n", ""))
    except Exception as exc:
        raise RuntimeError("failed to read back persisted trade review completion: " + str(exc)) from exc
    if verified_body != body:
        raise RuntimeError("persisted trade review completion readback mismatch: " + relative_path)
    return relative_path



def _wait_for_completion_push(completion_path: str, started_at: datetime) -> dict[str, object]:
    """Wait for the canonical push-triggered notification run for this completion."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        return {"submitted": False, "read_back": False, "reason": "missing GitHub credentials"}
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        return {"submitted": False, "read_back": False, "reason": "invalid GITHUB_REPOSITORY"}
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/decision-notification.yml/runs?event=push&branch=main&per_page=20"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    while datetime.now(timezone.utc) < deadline:
        try:
            req = urllib_request.Request(endpoint, method="GET", headers=headers)
            with urllib_request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            for run in data.get("workflow_runs", []):
                created_at = str(run.get("created_at") or "")
                if not created_at:
                    continue
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if created >= started_at and str(run.get("event") or "") == "push" and str(run.get("head_branch") or "") == "main":
                    return {"submitted": True, "read_back": True, "wake": "CANONICAL_COMPLETION_PUSH", "run_id": run.get("id"), "run_url": run.get("html_url"), "run_status": run.get("status"), "run_conclusion": run.get("conclusion"), "completion_path": completion_path}
        except Exception:
            pass
        time.sleep(3)
    return {"submitted": False, "read_back": False, "wake": "CANONICAL_COMPLETION_PUSH", "completion_path": completion_path, "reason": "canonical completion push notification run not read back"}


def _dispatch_notification(completion_path: str) -> dict[str, object]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        return {"submitted": False, "reason": "missing GITHUB_TOKEN or GITHUB_REPOSITORY"}
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        return {"submitted": False, "reason": "invalid GITHUB_REPOSITORY"}
    payload = json.dumps({
        "ref": "main",
        "inputs": {"scheduled_review_completion_path": completion_path},
    }).encode("utf-8")
    base = "https://api.github.com/repos/" + owner + "/" + repo
    dispatch_endpoint = base + "/actions/workflows/decision-notification.yml/dispatches"
    runs_endpoint = base + "/actions/workflows/decision-notification.yml/runs?event=workflow_dispatch&branch=main&per_page=20"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    dispatched_at = datetime.now(timezone.utc)
    try:
        req = urllib_request.Request(dispatch_endpoint, data=payload, method="POST", headers=headers)
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
                if created >= dispatched_at and str(run.get("event") or "") == "workflow_dispatch" and str(run.get("head_branch") or "") == "main":
                    return {
                        "submitted": True,
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
    parser.add_argument("--market-date", default="")
    parser.add_argument("--parent-request-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--final-content", required=True)
    parser.add_argument("--task-id", default="ETF交易复盘")
    parser.add_argument("--task-run-id", default="")
    parser.add_argument("--occurrence-id", default="")
    parser.add_argument(
        "--entry-mode",
        required=True,
        choices=("MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"),
    )
    args = parser.parse_args()

    task_run_id, occurrence_id = derive_runtime_identity(
        args.task_id,
        args.request_id,
        args.requested_at_beijing,
        args.entry_mode,
        args.task_run_id,
        args.occurrence_id,
    )

    target = write_completion_request(
        args.formal_review,
        args.request_id,
        args.requested_at_beijing,
        args.market_date,
        args.parent_request_id,
        args.output,
        args.final_content,
        args.task_id,
        task_run_id,
        occurrence_id,
        args.entry_mode,
    )
    completion_path = Path(target)
    if not completion_path.exists():
        raise RuntimeError("trade review completion write did not produce a readable file")
    persisted = json.loads(completion_path.read_text(encoding="utf-8"))
    binding = persisted.get("presentation_binding") or {}
    expected_hash = hashlib.sha256(args.final_content.encode("utf-8")).hexdigest()
    checks = {
        "request_id": persisted.get("request_id") == args.request_id,
        "task_run_id": persisted.get("task_run_id") == task_run_id,
        "occurrence_id": persisted.get("occurrence_id") == occurrence_id,
        "entry_mode": persisted.get("entry_mode") == args.entry_mode,
        "report_type": binding.get("report_type") == "ETF_TRADE_REVIEW",
        # Hash is retained for audit only and must not block the same
        # frozen body from reaching the existing notification owner.
        "full_content": binding.get("full_content") == args.final_content,
    }
    if not all(checks.values()):
        failed = ", ".join(key for key, ok in checks.items() if not ok)
        raise RuntimeError(f"trade review completion readback mismatch: {failed}")
    persisted_path, persistence = persist_completion_with_recovery(Path(target))
    push_started_at = datetime.now(timezone.utc)
    canonical_path = _persist_completion_to_main(persisted_path)
    push = _wait_for_completion_push(canonical_path, push_started_at)
    dispatch = push if push.get("read_back") else _dispatch_notification(canonical_path)
    # Same-run hard gate: completion persistence alone is not a successful
    # handoff. The actor must dispatch the canonical notification workflow and
    # read back the matching run before reporting success.
    handoff_ok = bool(dispatch.get("submitted")) and bool(dispatch.get("read_back"))
    result = {
        "ok": handoff_ok,
        "completion_path": persisted_path,
        "persistence": persistence,
        "readback": checks,
        "notification_dispatch": dispatch,
        "same_run_handoff_complete": handoff_ok,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if handoff_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
