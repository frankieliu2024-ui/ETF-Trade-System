"""Run the canonical Trade Review Actor handoff.

This is the single production adapter between a completed Trade Review run and
the existing Scheduled Review completion builder.  It does not build a second
payload, publish events, send notifications, or re-analyse the review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from scheduled_review_completion import write_completion_request
    from task_runtime_identity import derive_runtime_identity
except ModuleNotFoundError:
    from scripts.scheduled_review_completion import write_completion_request
    from scripts.task_runtime_identity import derive_runtime_identity


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
    target = write_completion_request(
        args.formal_review,
        args.request_id,
        args.requested_at_beijing,
        args.market_date,
        args.parent_request_id,
        args.output,
        args.final_content,
        args.task_id,
        args.task_run_id,
        args.occurrence_id,
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
        "content_hash": binding.get("content_hash") == expected_hash,
        "full_content": binding.get("full_content") == args.final_content,
    }
    if not all(checks.values()):
        failed = ", ".join(key for key, ok in checks.items() if not ok)
        raise RuntimeError(f"trade review completion readback mismatch: {failed}")
    print(json.dumps({"ok": True, "completion_path": str(target), "readback": checks}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
