from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from notification_center import build_report_delivery_request, validate_report_delivery_request


SOURCE_REQUEST_TYPE = "SCHEDULED_REPORT_SOURCE"
SOURCE_REQUIRED_FIELDS = {
    "request_type", "task_id", "task_run_id", "report_id", "report_type",
    "effective_market_date", "generated_at", "full_content",
}


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


def project_scheduled_report_source(*, source_path: Path, root: Path = Path(".")) -> Path:
    """Project a schema-minimal Scheduled Actor source into the canonical REPORT request."""
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError("scheduled REPORT source must be a JSON object")
    missing = sorted(SOURCE_REQUIRED_FIELDS - set(source))
    if missing:
        raise ValueError("scheduled REPORT source missing required fields: " + ", ".join(missing))
    if source.get("request_type") != SOURCE_REQUEST_TYPE:
        raise ValueError("unsupported scheduled REPORT source request_type")
    report_type = str(source.get("report_type") or "")
    if report_type not in {"ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW"}:
        raise ValueError("unsupported scheduled REPORT source report_type")
    report_id = str(source.get("report_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", report_id):
        raise ValueError("report_id must be path-safe")
    body = str(source.get("full_content") or "")
    if not body.strip():
        raise ValueError("scheduled REPORT source full_content must be non-empty")
    first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    title = first_line[:120] or ("【ETF交易复盘】" if report_type == "ETF_TRADE_REVIEW" else "【ETF系统复核】")
    summary = "Scheduled ETF trade review" if report_type == "ETF_TRADE_REVIEW" else "Scheduled ETF system review"
    task_run_id = str(source.get("task_run_id") or "")
    output = root / "requests" / "report_delivery" / f"{report_id}.json"
    build_and_write_report_request(
        output=output,
        task_id=str(source.get("task_id") or ""),
        task_run_id=task_run_id,
        report_id=report_id,
        report_type=report_type,
        effective_market_date=str(source.get("effective_market_date") or ""),
        title=title,
        summary=summary,
        full_content=body,
        source_reference=f"scheduled-review:{task_run_id}",
        idempotency_key=f"{report_type}:{task_run_id}",
        generated_at=str(source.get("generated_at") or ""),
        source_actor="ChatGPT",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical ingress for Scheduled Actor REPORT delivery requests.")
    parser.add_argument("--source-request")
    parser.add_argument("--output")
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

    if args.source_request:
        output = project_scheduled_report_source(source_path=Path(args.source_request), root=Path("."))
        print(str(output))
        return 0

    required = {
        "--output": args.output, "--task-id": args.task_id, "--task-run-id": args.task_run_id,
        "--report-id": args.report_id, "--report-type": args.report_type,
        "--effective-market-date": args.effective_market_date, "--title": args.title,
        "--summary": args.summary, "--full-content-file": args.full_content_file,
        "--source-reference": args.source_reference, "--idempotency-key": args.idempotency_key,
        "--generated-at": args.generated_at,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error("missing arguments for direct canonical ingress: " + ", ".join(missing))

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
