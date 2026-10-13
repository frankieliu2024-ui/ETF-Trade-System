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
    source = str(payload.get("source") or "").strip().upper()
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
