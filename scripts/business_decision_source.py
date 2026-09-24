"""Canonical Business Decision Source contract.

This module is deliberately side-effect free: it classifies and validates the
ChatGPT-authored business decision before the existing state-sync consumer
projects it into legacy Formal Completion.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

BUSINESS_DECISION_SOURCE = "BUSINESS_DECISION_SOURCE"
STATE_SYNC_ONLY = "STATE_SYNC_ONLY"

_REQUIRED = (
    "risk_permission", "main_candidate", "opportunity_status", "capital_use",
    "continued_holding_opportunity_cost", "action_changes_now",
    "next_change_condition", "capital_competition", "next_unit_capital_use",
)

def classify_request(request: dict[str, Any]) -> str:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    explicit = str(request.get("request_type") or "").strip().upper()
    if explicit == BUSINESS_DECISION_SOURCE:
        return BUSINESS_DECISION_SOURCE
    if explicit == STATE_SYNC_ONLY:
        return STATE_SYNC_ONLY
    return "REFRESH_BEARING"

def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def source_fingerprint(source: dict[str, Any]) -> str:
    body = {k: v for k, v in source.items() if k not in {"fingerprint", "status", "projection_status"}}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

def validate_source(source: dict[str, Any], *, expected_snapshot: str | None = None) -> dict[str, Any]:
    if classify_request(source) != BUSINESS_DECISION_SOURCE:
        raise ValueError("business source requires explicit BUSINESS_DECISION_SOURCE")
    if not str(source.get("request_id") or "").strip():
        raise ValueError("business source requires request_id")
    if not str(source.get("decision_id") or "").strip():
        raise ValueError("business source requires decision_id")
    snapshot = str(source.get("consumed_snapshot") or "").strip()
    if not snapshot or expected_snapshot and snapshot != expected_snapshot:
        raise ValueError("business source PIT binding failed")
    missing = [key for key in _REQUIRED if key not in source or source[key] in (None, "", [], {})]
    if missing:
        raise ValueError("business decision completeness failed: " + ",".join(missing))
    source = json.loads(json.dumps(source))
    source["source_type"] = BUSINESS_DECISION_SOURCE
    source["fingerprint"] = source_fingerprint(source)
    source["phase1_status"] = "PASS"
    source["source_durable"] = True
    source["reply_ready"] = True
    source["projection_status"] = "PENDING"
    return source

def build_formal_completion_from_source(source: dict[str, Any]) -> dict[str, Any]:
    checked = validate_source(source)
    result = json.loads(json.dumps(checked))
    # These three structures are transport projections only. Each must already
    # be present in the Source; no account/market fact may be used to create or
    # complete an investment judgment.
    for field in ("managed_position_reviews", "etf_opportunity_reviews", "capital_competition"):
        value = checked.get(field)
        if field == "capital_competition":
            valid = isinstance(value, (dict, list))
        else:
            valid = isinstance(value, list)
        if not valid:
            raise ValueError(f"{field} projection is ambiguous")
        result[field] = json.loads(json.dumps(value))
    result["request_type"] = STATE_SYNC_ONLY
    result["source"] = "CHATGPT_BUSINESS_DECISION_PROJECTION"
    result["projection_status"] = "READY"
    result["reply_ready"] = True
    result["formal_projection_pending"] = False
    return result
