"""Run the canonical production handoff for ETF System Review.

This adapter owns only the system-review completion handoff. It builds one
system-review completion, persists and reads it back, explicitly dispatches
the existing notification workflow with that exact completion path, and
reports the read-back boundary. It never creates a second business payload.
"""
from __future__ import annotations

import argparse
import base64
import time
from datetime import datetime, timezone
from urllib import request as urllib_request
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scheduled_review_completion import build_system_review_completion_request
from scripts.scheduled_review_completion_owner import persist_completion, dispatch_notification, read_main_json, contains_text
from scripts.task_runtime_identity import derive_runtime_identity



def _wait_for_system_projection(occurrence_id: str, report_id: str) -> dict[str, object]:
    event_path = "events/reviews/system_" + occurrence_id + ".json"
    report_path = "requests/report_delivery/" + str(report_id).replace(":", "__") + ".json"
    import time
    deadline = time.time() + 120
    while time.time() < deadline:
        event = read_main_json(event_path)
        report = read_main_json(report_path)
        center = read_main_json("data/state/notification_center.json")
        if isinstance(event, dict) and isinstance(report, dict) and isinstance(center, dict) and contains_text(center, report_id):
            return {"verified": True, "event_path": event_path, "report_path": report_path, "report_id": report_id}
        time.sleep(3)
    return {"verified": False, "event_path": event_path, "report_path": report_path, "report_id": report_id,
            "reason": "system event, REPORT, and bound notification record were not all readable"}

def _readback_notification_run(completion_path: str, submitted_after: datetime) -> dict[str, object]:
    """Confirm one workflow_dispatch run bound to this exact completion path."""
    token = (os.environ.get("ETF_GITHUB_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "frankieliu2024-ui/ETF-Trade-System")
    if not token or "/" not in repository:
        return {"verified": False, "reason": "GitHub run-readback credentials unavailable"}
    endpoint = f"https://api.github.com/repos/{repository}/actions/workflows/decision-notification.yml/runs?event=workflow_dispatch&branch=main&per_page=100"
    headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}
    deadline = time.monotonic() + 60
    expected_title = "ETF decision notification — " + completion_path
    while time.monotonic() < deadline:
        try:
            with urllib_request.urlopen(urllib_request.Request(endpoint, headers=headers), timeout=20) as response:
                runs = json.loads(response.read().decode("utf-8")).get("workflow_runs", [])
            matches = []
            for run in runs:
                created_at = str(run.get("created_at") or "")
                if not created_at:
                    continue
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if (created >= submitted_after and run.get("event") == "workflow_dispatch"
                        and run.get("head_branch") == "main" and run.get("display_title") == expected_title):
                    matches.append(run)
            if len(matches) == 1:
                run = matches[0]
                status = str(run.get("status") or "").lower()
                conclusion = str(run.get("conclusion") or "").lower()
                if status == "completed" and conclusion == "success":
                    return {"verified": True, "run_id": run.get("id"), "status": status, "conclusion": conclusion}
                if status == "completed" and conclusion not in {"", "null"}:
                    return {"verified": False, "run_id": run.get("id"), "status": status, "conclusion": conclusion,
                            "reason": "matching notification workflow run completed unsuccessfully"}
            if len(matches) > 1:
                return {"verified": False, "reason": "ambiguous notification workflow runs for completion"}
        except Exception:
            pass
        time.sleep(3)
    return {"verified": False, "reason": "matching notification workflow run not read back"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--final-content", required=True)
    parser.add_argument("--requested-at-beijing", required=True)
    parser.add_argument("--entry-mode", required=True,
                        choices=("MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"))
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--task-run-id", default="")
    parser.add_argument("--occurrence-id", default="")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--review-json", default="")
    args = parser.parse_args()

    task_run_id, occurrence_id = derive_runtime_identity(
        "ETF系统复核",
        args.request_id or args.task_run_id or args.occurrence_id or "system-review-runtime",
        args.requested_at_beijing, args.entry_mode,
        args.task_run_id, args.occurrence_id,
    )
    if not args.review_json:
        raise ValueError("system review requires an explicit same-run review JSON; default PASS is prohibited")
    review = json.loads(Path(args.review_json).read_text(encoding="utf-8"))
    if not isinstance(review, dict) or review.get("task_id") != "ETF系统复核" or review.get("market_date") != args.market_date:
        raise ValueError("system review input identity or market_date mismatch")
    payload = build_system_review_completion_request(
        review,
        args.request_id or task_run_id,
        args.requested_at_beijing,
        "ETF系统复核",
        task_run_id,
        args.final_content,
        args.entry_mode,
        occurrence_id,
    )
    target = Path(args.output).resolve() if args.output else (
        ROOT / "requests" / "live_snapshot" /
        f"{task_run_id}__scheduled_review_completion.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    persisted = json.loads(target.read_text(encoding="utf-8"))
    binding = persisted.get("presentation_binding") or {}
    expected_hash = hashlib.sha256(args.final_content.encode("utf-8")).hexdigest()
    checks = {
        "task_id": persisted.get("task_id") == "ETF系统复核",
        "request_type": persisted.get("request_type") == "SCHEDULED_REVIEW_COMPLETION",
        "formal_fact_type": persisted.get("formal_fact_type") == "FORMAL_SCHEDULED_SYSTEM_REVIEW",
        "report_type": binding.get("report_type") == "ETF_SYSTEM_REVIEW",
        "full_content": binding.get("full_content") == args.final_content,
        "content_hash": binding.get("content_hash") == expected_hash,
        "occurrence_id": persisted.get("occurrence_id") == occurrence_id,
    }
    if not all(checks.values()):
        raise RuntimeError("system review completion readback mismatch: " +
                           ", ".join(k for k, v in checks.items() if not v))

    completion_path, persistence = persist_completion(target)
    dispatch_started_at = datetime.now(timezone.utc)
    dispatch = dispatch_notification(completion_path)
    workflow_run = _readback_notification_run(completion_path, dispatch_started_at)
    projection = _wait_for_system_projection(occurrence_id, binding.get("report_id") or "")
    # Dispatch acceptance is not a workflow-run readback. Verify downstream projections separately.
    ok = bool(dispatch.get("submitted")) and bool(workflow_run.get("verified")) and bool(projection.get("verified"))
    print(args.final_content)
    print(json.dumps({
        "ok": ok,
        "user_visible_content": args.final_content,
        "completion_path": completion_path,
        "persistence": persistence,
        "readback": checks,
        "notification_dispatch": dispatch,
        "notification_workflow_run": workflow_run,
        "projection": projection,
        "same_run_handoff_complete": ok,
    }, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
