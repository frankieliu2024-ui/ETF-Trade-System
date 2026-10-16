"""Shared Formal Decision entry identity contract.

This module is validation-only. It does not create requests, facts, permissions,
orders, or notification events. All existing producers must use the same identity
fields before entering the canonical Formal Decision pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass


ENTRY_MANUAL_CONVERSATION = "MANUAL_CONVERSATION"
ENTRY_MANUAL_SCHEDULED_TASK = "MANUAL_SCHEDULED_TASK"
ENTRY_AUTOMATIC_SCHEDULED_TASK = "AUTOMATIC_SCHEDULED_TASK"

_ALLOWED = {
    ENTRY_MANUAL_CONVERSATION,
    ENTRY_MANUAL_SCHEDULED_TASK,
    ENTRY_AUTOMATIC_SCHEDULED_TASK,
}

FORMAL_REQUEST_SOURCES = {
    "CHATGPT_MANUAL_FORMAL_ANALYSIS",
    "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
    "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
}

CANONICAL_PARENT_REQUEST_TYPE = "MARKET_QUOTE_REFRESH"
CANONICAL_PARENT_QUERY_INTENT = "FORMAL_INTRADAY_ANALYSIS"
CANONICAL_COMPLETION_REQUEST_TYPE = "STATE_SYNC_ONLY"
CANONICAL_COMPLETION_FACT_TYPE = "FORMAL_DECISION_COMPLETION"
CANONICAL_COMPLETION_SOURCE = "CHATGPT_FORMAL_DECISION_COMPLETION"
CANONICAL_SCENARIO = "FORMAL_DECISION"
CANONICAL_REPORT_TYPE = "ETF_FORMAL_DECISION"
CANONICAL_TASK_ID = "ETF正式决策"


def validate_formal_parent_request(payload: dict) -> tuple[bool, str]:
    """Validate the one canonical Formal Decision parent request."""
    if not isinstance(payload, dict):
        return False, "payload_must_be_object"
    if str(payload.get("request_type") or "").strip().upper() != CANONICAL_PARENT_REQUEST_TYPE:
        return False, "formal_parent_request_type_must_be_MARKET_QUOTE_REFRESH"
    intent = payload.get("intent")
    query_intent = intent.get("query_intent") if isinstance(intent, dict) else payload.get("query_intent") or intent
    if str(query_intent or "").strip().upper() != CANONICAL_PARENT_QUERY_INTENT:
        return False, "formal_parent_query_intent_must_be_FORMAL_INTRADAY_ANALYSIS"
    for field, reason in (
        ("force_refresh", "formal_parent_force_refresh_required"),
        ("require_post_request_snapshot", "formal_parent_post_request_snapshot_required"),
        ("reuse_inflight_refresh", "formal_parent_reuse_inflight_refresh_required"),
    ):
        if payload.get(field) is not True:
            return False, reason
    return validate_formal_entry_identity(payload)


def validate_formal_completion_request(payload: dict) -> tuple[bool, str]:
    """Validate the canonical post-BDS Formal Decision completion ingress."""
    if not isinstance(payload, dict):
        return False, "payload_must_be_object"
    if str(payload.get("request_type") or "").strip().upper() != CANONICAL_COMPLETION_REQUEST_TYPE:
        return False, "formal_completion_request_type_must_be_STATE_SYNC_ONLY"
    if str(payload.get("formal_fact_type") or "").strip().upper() != CANONICAL_COMPLETION_FACT_TYPE:
        return False, "formal_fact_type_must_be_FORMAL_DECISION_COMPLETION"
    if str(payload.get("source") or "").strip().upper() != CANONICAL_COMPLETION_SOURCE:
        return False, "formal_source_must_be_CHATGPT_FORMAL_DECISION_COMPLETION"
    valid, reason = validate_formal_entry_identity(payload)
    if not valid:
        return False, reason
    if not str(payload.get("parent_request_id") or "").strip():
        return False, "formal_completion_parent_request_id_required"
    if str(payload.get("request_id") or "").strip() == str(payload.get("parent_request_id") or "").strip():
        return False, "formal_completion_request_id_must_differ_from_parent"
    for field, reason in (
        ("bds_path", "formal_completion_bds_path_required"),
        ("decision_id", "formal_completion_decision_id_required"),
    ):
        if not str(payload.get(field) or "").strip():
            return False, reason
    if len(str(payload.get("content_hash") or "").strip()) != 64:
        return False, "formal_completion_content_hash_required"
    return True, ""


def validate_canonical_formal_request(payload: dict) -> tuple[bool, str]:
    """Backward-compatible name for the canonical Formal Decision completion validator."""
    return validate_formal_completion_request(payload)
