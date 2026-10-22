"""Run the canonical Trade Review Actor handoff.

This is the single production adapter between a completed Trade Review run and
the existing Scheduled Review completion builder.  It does not build a second
payload, publish events, send notifications, or re-analyse the review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from scheduled_review_completion import write_completion_request
except ModuleNotFoundError:
    from scripts.scheduled_review_completion import write_completion_request


def _derive_run_identity(request_id: str, requested_at_beijing: str, entry_mode: str) -> tuple[str, str]:
    """Create one auditable identity when the task platform omits run IDs."""
    seed = f"{request_id}|{requested_at_beijing}|{entry_mode}".encode("utf-8")
    suffix = hashlib.sha256(seed).hexdigest()[:12]
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(requested_at_beijing))
    prefix = "automatic" if entry_mode == "AUTOMATIC_SCHEDULED_TASK" else "manual"
    run_id = f"trade-review-{prefix}-{stamp}-{suffix}"
    return run_id, run_id


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
    task_run_id = args.task_run_id
    occurrence_id = args.occurrence_id
    if not task_run_id or not occurrence_id:
        derived_run_id, derived_occurrence_id = _derive_run_identity(
            args.request_id, args.requested_at_beijing, args.entry_mode
        )
        task_run_id = task_run_id or derived_run_id
        occurrence_id = occurrence_id or derived_occurrence_id
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
        "source": persisted.get("source") == "CHATGPT_SCHEDULED_TRADE_REVIEW_ACTOR",
        "interaction_scenario": persisted.get("interaction_scenario") == "SCHEDULED_TRADE_REVIEW",
        "report_type": binding.get("report_type") == "ETF_TRADE_REVIEW",
        "content_hash": binding.get("content_hash") == expected_hash,
        "full_content": binding.get("full_content") == args.final_content,
    }
    if not all(checks.values()):
        failed = ", ".join(key for key, ok in checks.items() if not ok)
        raise RuntimeError(f"trade review completion readback mismatch: {failed}")
    print(json.dumps({
        "ok": True,
        "completion_path": str(target),
        "task_run_id": task_run_id,
        "occurrence_id": occurrence_id,
        "readback": checks,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
