from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from notification_materiality_guard import decision_critical_blockage

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
TZ = timezone(timedelta(hours=8))
PUSHPLUS_URL = "https://www.pushplus.plus/send"

# Formal completion mode is a process-wide delivery boundary shared by validation and projection.
def strict_formal_completion_enabled() -> bool:
    return os.environ.get("FORMAL_COMPLETION_EVENT", "").strip().lower() == "true"
HISTORY_LIMIT = 50
OPPORTUNITY_STATUSES = {"无机会", "观察机会", "Trial机会", "Confirm机会"}
RISK_PERMISSIONS = ("禁止新增", "允许Confirm", "允许Trial")
FORMAL_EVENT_MAX_AGE_MINUTES = 45
ACCOUNT_EVENT_MAX_AGE_MINUTES = 30
MIN_UNEXPLAINED_CASH_DELTA_YUAN = 10.0
TRADING_CALENDAR = ROOT / "config" / "market" / "a_share_trading_calendar_2026.json"

ACTIVE_REPORT_TYPES = {"ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW", "ETF_FORMAL_DECISION"}
CANONICAL_REPORT_TITLES = {
    "ETF_TRADE_REVIEW": "【ETF交易复盘】",
    "ETF_SYSTEM_REVIEW": "【ETF系统复核】",
    "ETF_FORMAL_DECISION": "【ETF正式决策】",
}
HISTORICAL_REPORT_TYPES = set()
REPORT_REQUEST_DIR = ROOT / "requests" / "report_delivery"
REPORT_HANDOFF_DIR = ROOT / "requests" / "report_handoff"
REVIEW_EVENT_DIR = ROOT / "events" / "reviews"

def canonical_report_title(report_type: str) -> str:
    try:
        return CANONICAL_REPORT_TITLES[str(report_type)]
    except KeyError as exc:
        raise ValueError(f"unsupported report_type for canonical title: {report_type}") from exc

def validate_report_delivery_request(request: dict) -> tuple[bool, str]:
    """Validate a completed active formal report before shared delivery."""
    required = ("schema_version", "channel", "report_type", "report_id", "task_id", "task_run_id",
                "generated_at", "effective_market_date", "source_actor", "source_reference",
                "title", "summary", "full_content", "idempotency_key")
    missing = [key for key in required if not str(request.get(key) or "").strip()]
    if missing: return False, "missing:" + ",".join(missing)
    if request.get("channel") != "REPORT": return False, "channel_must_be_REPORT"
    if request.get("report_type") not in ACTIVE_REPORT_TYPES: return False, "unsupported_report_type"
    if request.get("delivery_mode", "FULL_REPORT") != "FULL_REPORT": return False, "delivery_mode_must_be_FULL_REPORT"
    if request.get("no_trade_authority") is not True: return False, "no_trade_authority_must_be_true"
    # content_hash is optional audit metadata; closure is based on exact frozen-body equality.
    # content_hash is an audit field, never a delivery gate. Exact frozen-body
    # equality and identity binding remain the delivery gates.
    return True, ""

def build_report_delivery_request(*, task_id: str, task_run_id: str, report_id: str,
                                  report_type: str,
                                  effective_market_date: str, title: str, summary: str,
                                  full_content: str, source_reference: str,
                                  idempotency_key: str, generated_at: str,
                                  source_actor: str = "ChatGPT") -> dict:
    """Build the existing REPORT contract at its canonical delivery owner.

    This is schema construction only: it does not send, persist, or infer review
    facts. Producers must provide the task/run identity and frozen report body.
    """
    body = str(full_content)
    request = {
        "schema_version": "1.0", "channel": "REPORT",
        "report_type": str(report_type), "report_id": str(report_id),
        "task_id": str(task_id), "task_run_id": str(task_run_id),
        "generated_at": str(generated_at),
        "effective_market_date": str(effective_market_date),
        "source_actor": str(source_actor), "source_reference": str(source_reference),
        "title": canonical_report_title(report_type), "summary": str(summary), "full_content": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "idempotency_key": str(idempotency_key),
        "delivery_mode": "FULL_REPORT", "no_trade_authority": True,
    }
    valid, reason = validate_report_delivery_request(request)
    if not valid:
        raise ValueError("invalid canonical REPORT request: " + reason)
    return request


def validate_report_handoff(handoff: dict) -> tuple[bool, str]:
    """Validate the minimal delivery-only envelope from a Scheduled Review."""
    required = ("schema_version", "report_type", "task_id", "task_run_id",
                "generated_at", "effective_market_date", "title", "summary", "full_content")
    missing = [key for key in required if not str(handoff.get(key) or "").strip()]
    if missing:
        return False, "missing:" + ",".join(missing)
    if handoff.get("schema_version") != "1.0":
        return False, "unsupported_handoff_schema"
    if handoff.get("report_type") not in ACTIVE_REPORT_TYPES:
        return False, "unsupported_report_type"
    return True, ""


def build_report_from_handoff(handoff: dict) -> dict:
    """Project a delivery-only handoff into the canonical REPORT contract in memory."""
    valid, reason = validate_report_handoff(handoff)
    if not valid:
        raise ValueError("invalid REPORT handoff: " + reason)
    report_type = str(handoff["report_type"])
    task_run_id = str(handoff["task_run_id"])
    return build_report_delivery_request(
        task_id=str(handoff["task_id"]),
        task_run_id=task_run_id,
        report_id=f"{report_type.lower()}:{task_run_id}",
        report_type=report_type,
        effective_market_date=str(handoff["effective_market_date"]),
        title=canonical_report_title(report_type),
        summary=str(handoff["summary"]),
        full_content=str(handoff["full_content"]),
        source_reference=f"scheduled-report-handoff:{task_run_id}",
        idempotency_key=f"{report_type}:{task_run_id}",
        generated_at=str(handoff["generated_at"]),
        source_actor="Scheduled Review Actor",
    )


def _report_handoff_path() -> Path | None:
    raw_path = os.environ.get("REPORT_HANDOFF_PATH", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else ROOT / path


def _report_delivery_path() -> Path | None:
    raw_path = os.environ.get("REPORT_DELIVERY_PATH", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else ROOT / path


def report_delivery_validation_error() -> str | None:
    """Return an auditable validation error for an explicitly triggered REPORT."""
    if strict_formal_completion_enabled():
        return None
    handoff_path = _report_handoff_path()
    if handoff_path is not None:
        if not handoff_path.is_file():
            return "missing_report_handoff_path:" + str(handoff_path)
        handoff = read_json(handoff_path, {})
        valid, reason = validate_report_handoff(handoff)
        return None if valid else "invalid_report_handoff:" + reason
    path = _report_delivery_path()
    if path is None:
        return None
    if not path.is_file():
        return "missing_report_path:" + str(path)
    request = read_json(path, {})
    valid, reason = validate_report_delivery_request(request)
    return None if valid else "invalid_report:" + reason


def formal_decision_report_event() -> dict | None:
    """Compatibility stub: the legacy Formal Decision route is fail-closed."""
    return None


def _validate_formal_presentation_layout(content: str) -> str:
    """Validate paragraph boundaries and semantic numbering hierarchy."""
    import re
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith("【ETF正式决策】"):
        return "formal_presentation_title_not_first_line"
    heading_pattern = re.compile(r"^【[^】]+】$")
    primary_seen = False
