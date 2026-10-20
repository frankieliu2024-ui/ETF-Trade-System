"""Run the canonical Trade Review Actor handoff.

This is the single production adapter between a completed Trade Review run and
the existing Scheduled Review completion builder.  It does not build a second
payload, publish events, send notifications, or re-analyse the review.
"""
from __future__ import annotations

import argparse
import json

try:
    from scheduled_review_completion import write_completion_request
except ModuleNotFoundError:
    from scripts.scheduled_review_completion import write_completion_request


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
    parser.add_argument("--task-run-id", required=True)
    parser.add_argument("--occurrence-id", required=True)
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
    print(json.dumps({"ok": True, "completion_path": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
