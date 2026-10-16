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

CANONICAL_REQUEST_TYPE = "STATE_SYNC_ONLY"
CANONICAL_FACT_TYPE = "FORMAL_SCHEDULED_REVIEW"
CANONICAL_ACTOR_SOURCE = "CHATGPT_SCHEDULED_REVIEW_ACTOR"
CANONICAL_SCENARIO = "FORMAL_DECISION"
CANONICAL_REPORT_TYPE = "ETF_FORMAL_DECISION"


def validate_canonical_formal_request(payload: dict) -> tuple[bool, str]:
    """Validate the single production ingress shared by all three entries.

    Legacy refresh/BDS/completion envelopes remain readable for historical
    diagnostics, but this validator is the only contract allowed to enter the
    current Formal Decision review-event pipeline.
    """
    if not isinstance(payload, dict):
        return False, "payload_must_be_object"
    if str(payload.get("request_type") or "").strip().upper() != CANONICAL_REQUEST_TYPE:
        return False, "formal_request_type_must_be_STATE_SYNC_ONLY"
    if str(payload.get("formal_fact_type") or "").strip().upper() != CANONICAL_FACT_TYPE:
        return False, "formal_fact_type_must_be_FORMAL_SCHEDULED_REVIEW"
    if str(payload.get("source") or "").strip().upper() != CANONICAL_ACTOR_SOURCE:
        return False, "formal_source_must_be_CHATGPT_SCHEDULED_REVIEW_ACTOR"
    if str(payload.get("interaction_scenario") or "").strip().upper() != CANONICAL_SCENARIO:
        return False, "formal_interaction_scenario_must_be_FORMAL_DECISION"
    valid, reason = validate_formal_entry_identity(payload)
    if not valid:
        return False, reason
    binding = payload.get("presentation_binding")
    if not isinstance(binding, dict):
        return False, "presentation_binding_must_be_object"
    required = ("report_type", "task_id", "task_run_id", "report_id", "full_content", "content_hash", "market_date")
    missing = [key for key in required if not str(binding.get(key) or "").strip()]
    if missing:
        return False, "presentation_binding_missing:" + ",".join(missing)
    if str(binding.get("report_type") or "").strip() != CANONICAL_REPORT_TYPE:
        return False, "presentation_binding_report_type_mismatch"
    if str(binding.get("task_id") or "").strip() != str(payload.get("task_id") or "").strip():
        return False, "presentation_binding_task_id_mismatch"
    if str(binding.get("task_run_id") or "").strip() != str(payload.get("task_run_id") or "").strip():
        return False, "presentation_binding_task_run_id_mismatch"
    if str(binding.get("market_date") or "").strip() != str(payload.get("market_date") or "").strip():
        return False, "presentation_binding_market_date_mismatch"
    if str(payload.get("occurrence_id") or payload.get("occurrence") or "").strip() != str(binding.get("occurrence_id") or payload.get("occurrence_id") or payload.get("occurrence") or "").strip():
        return False, "presentation_binding_occurrence_mismatch"
    return True, ""


@dataclass(frozen=True)
class FormalEntryIdentity:
    entry_mode: str
    request_id: str
    task_id: str
    task_run_id: str
    occurrence: str
    market_date: str

    @property
    def dedup_key(self) -> str:
        return ":".join(
            (self.market_date, self.task_id, self.entry_mode, self.occurrence)
        )


def validate_formal_entry_identity(payload: dict) -> tuple[bool, str]:
    """Validate identity before any request-bound state is produced."""
    mode = str(payload.get("entry_mode") or "").strip().upper()
    if mode not in _ALLOWED:
        return False, "invalid_entry_mode"
    required = ("request_id", "task_id", "task_run_id", "occurrence", "market_date")
    missing = [name for name in required if not str(payload.get(name) or "").strip()]
    if missing:
        return False, "missing:" + ",".join(missing)
    source = str(payload.get("trigger_source") or payload.get("source") or "").strip().upper()
    expected = {
        ENTRY_MANUAL_CONVERSATION: "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        ENTRY_MANUAL_SCHEDULED_TASK: "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
        ENTRY_AUTOMATIC_SCHEDULED_TASK: "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
    }[mode]
    if source != expected:
        return False, "source_entry_mode_mismatch"
    if mode == ENTRY_AUTOMATIC_SCHEDULED_TASK and not str(payload.get("occurrence") or "").startswith("scheduled_"):
        return False, "automatic_occurrence_must_be_scheduled"
    if mode == ENTRY_MANUAL_SCHEDULED_TASK and str(payload.get("occurrence") or "").startswith("scheduled_"):
        return False, "manual_task_must_not_impersonate_automatic_occurrence"
    return True, ""


def build_identity(payload: dict) -> FormalEntryIdentity:
    valid, reason = validate_formal_entry_identity(payload)
    if not valid:
        raise ValueError(reason)
    return FormalEntryIdentity(
        entry_mode=str(payload["entry_mode"]).strip().upper(),
        request_id=str(payload["request_id"]).strip(),
        task_id=str(payload["task_id"]).strip(),
        task_run_id=str(payload["task_run_id"]).strip(),
        occurrence=str(payload["occurrence"]).strip(),
        market_date=str(payload["market_date"]).strip(),
    )
