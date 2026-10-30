"""Run the canonical production handoff for ETF System Review.

This adapter owns only the system-review completion handoff. It builds one
system-review completion, persists and reads it back, explicitly dispatches
the existing notification workflow with that exact completion path, and
reports the read-back boundary. It never creates a second business payload.
"""
from __future__ import annotations

import argparse
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
    dispatch = dispatch_notification(completion_path)
    projection = _wait_for_system_projection(occurrence_id, binding.get("report_id") or "")
    # Dispatch acceptance is not a workflow-run readback. Verify downstream projections separately.
    ok = bool(dispatch.get("submitted")) and bool(projection.get("verified"))
    print(args.final_content)
    print(json.dumps({
        "ok": ok,
        "user_visible_content": args.final_content,
        "completion_path": completion_path,
        "persistence": persistence,
        "readback": checks,
        "notification_dispatch": dispatch,
        "projection": projection,
        "same_run_handoff_complete": ok,
    }, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
