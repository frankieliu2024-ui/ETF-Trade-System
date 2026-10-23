"""Rebuilt Formal Decision actor using the verified Scheduled Trade Review handoff.

This adapter intentionally does not call BUSINESS_DECISION_SOURCE, legacy formal
replay/recovery, or any Formal Decision-specific projection. It freezes one
business review, writes one shared Scheduled Review completion, and leaves the
canonical event/report/notification owners unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from scheduled_review_completion import build_trade_review_completion_request
    from task_runtime_identity import derive_runtime_identity
except ModuleNotFoundError:
    from scripts.scheduled_review_completion import build_trade_review_completion_request
    from scripts.task_runtime_identity import derive_runtime_identity

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-review", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--requested-at-beijing", required=True)
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--final-content", required=True)
    parser.add_argument("--task-run-id", default="")
    parser.add_argument("--occurrence-id", default="")
    parser.add_argument("--entry-mode", required=True,
                        choices=("MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK",
                                 "AUTOMATIC_SCHEDULED_TASK"))
    args = parser.parse_args()

    review_path = (ROOT / args.formal_review).resolve()
    output_path = (ROOT / args.output).resolve()
    final_path = (ROOT / args.final_content).resolve()
    if ROOT not in review_path.parents or ROOT not in output_path.parents or ROOT not in final_path.parents:
        raise ValueError("paths must remain inside repository")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    content = final_path.read_text(encoding="utf-8")
    task_run_id, occurrence_id = derive_runtime_identity(
        "ETF正式决策", args.request_id, args.requested_at_beijing,
        args.entry_mode, args.task_run_id, args.occurrence_id
    )
    # Reuse the proven Scheduled Review schema validator and identity builder.
    payload = build_trade_review_completion_request(
        review, args.request_id, args.requested_at_beijing, args.market_date,
        final_content=content, task_id="ETF正式决策", task_run_id=task_run_id,
        occurrence_id=occurrence_id,
        entry_mode=("MANUAL_SCHEDULED_TASK" if args.entry_mode == "MANUAL_CONVERSATION"
                    else args.entry_mode),
    )
    payload["formal_fact_type"] = "FORMAL_POST_CLOSE_REVIEW"
    payload["interaction_scenario"] = "POST_CLOSE_REVIEW"
    payload["source"] = "CHATGPT_SCHEDULED_REVIEW_ACTOR"
    payload["formal_entry_source"] = "CHATGPT_SCHEDULED_FORMAL_ANALYSIS"
    binding = payload["presentation_binding"]
    binding["report_type"] = "ETF_FORMAL_DECISION"
    binding["report_id"] = f"ETF_FORMAL_DECISION:{task_run_id}"
    binding["task_id"] = "ETF正式决策"
    binding["content_hash"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    payload["presentation_binding"] = binding
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    if persisted.get("request_id") != args.request_id:
        raise RuntimeError("completion readback request mismatch")
    if persisted.get("task_id") != "ETF正式决策":
        raise RuntimeError("completion readback task mismatch")
    if (persisted.get("presentation_binding") or {}).get("report_type") != "ETF_FORMAL_DECISION":
        raise RuntimeError("completion readback report type mismatch")
    if (persisted.get("presentation_binding") or {}).get("full_content") != content:
        raise RuntimeError("completion readback content mismatch")
    print(json.dumps({"ok": True, "completion_path": args.output,
                      "task_run_id": task_run_id, "occurrence_id": occurrence_id,
                      "content_hash": binding["content_hash"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
