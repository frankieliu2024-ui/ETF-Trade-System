from __future__ import annotations

import argparse
import json
from pathlib import Path

from notification_center import build_report_delivery_request, validate_report_delivery_request


def build_and_write_report_request(*, output: Path, task_id: str, task_run_id: str,
                                   report_id: str, report_type: str,
                                   effective_market_date: str, title: str, summary: str,
                                   full_content: str, source_reference: str,
                                   idempotency_key: str, generated_at: str,
                                   source_actor: str = "ChatGPT") -> dict:
    """Persist a Scheduled Actor REPORT only through the canonical schema builder."""
    request = build_report_delivery_request(
        task_id=task_id,
        task_run_id=task_run_id,
        report_id=report_id,
        report_type=report_type,
        effective_market_date=effective_market_date,
        title=title,
        summary=summary,
        full_content=full_content,
        source_reference=source_reference,
        idempotency_key=idempotency_key,
        generated_at=generated_at,
        source_actor=source_actor,
    )
    valid, reason = validate_report_delivery_request(request)
    if not valid:
        raise ValueError("canonical REPORT validation failed before persistence: " + reason)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical ingress for Scheduled Actor REPORT delivery requests.")
    parser.add_argument("--source-request")\n    parser.add_argument("--output")
    parser.add_argument("--task-id")
    parser.add_argument("--task-run-id")
    parser.add_argument("--report-id")
    parser.add_argument("--report-type", choices=("ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW"))
    parser.add_argument("--effective-market-date")
    parser.add_argument("--title")
    parser.add_argument("--summary")
    parser.add_argument("--full-content-file")
    parser.add_argument("--source-reference")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--generated-at")
    parser.add_argument("--source-actor", default="ChatGPT")
    args = parser.parse_args()

    full_content = Path(args.full_content_file).read_text(encoding="utf-8")
    build_and_write_report_request(
        output=Path(args.output),
        task_id=args.task_id,
        task_run_id=args.task_run_id,
        report_id=args.report_id,
        report_type=args.report_type,
        effective_market_date=args.effective_market_date,
        title=args.title,
        summary=args.summary,
        full_content=full_content,
        source_reference=args.source_reference,
        idempotency_key=args.idempotency_key,
        generated_at=args.generated_at,
        source_actor=args.source_actor,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
