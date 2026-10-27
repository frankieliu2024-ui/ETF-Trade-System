"""Runtime identity adapter for the three registered ETF task chains.

It only fills IDs omitted by the task platform. It never infers the task route,
entry mode, source, business content, or completion state.
"""
from __future__ import annotations

import hashlib
import re


def derive_runtime_identity(task_id: str, request_id: str, requested_at_beijing: str,
                            entry_mode: str, task_run_id: str = "",
                            occurrence_id: str = "") -> tuple[str, str]:
    task = str(task_id or "").strip()
    request = str(request_id or "").strip()
    stamp = str(requested_at_beijing or "").strip()
    mode = str(entry_mode or "").strip().upper()
    if not task or not request or not stamp or not mode:
        raise ValueError("runtime identity derivation requires task_id, request_id, requested_at_beijing and entry_mode")
    supplied_run = str(task_run_id or "").strip()
    supplied_occurrence = str(occurrence_id or "").strip()
    digest = hashlib.sha256(f"{task}|{request}|{stamp}|{mode}".encode("utf-8")).hexdigest()[:12]
    if supplied_run and supplied_occurrence:
        return supplied_run, supplied_occurrence
    if mode == "AUTOMATIC_SCHEDULED_TASK":
        prefix = "scheduled_"
    elif mode == "MANUAL_CONVERSATION":
        prefix = "conversation_"
    else:
        prefix = "manual_"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", task).strip("-").lower() or "etf-task"
    generated = f"{prefix}{stem}_{digest}"
    return supplied_run or generated, supplied_occurrence or generated


def derive_request_identity(task_id: str, requested_at_beijing: str,
                           entry_mode: str, request_id: str = "") -> str:
    """Return the canonical request identity for a real task invocation.

    A platform-supplied request_id is authoritative. When a task surface does
    not expose one, derive a deterministic per-invocation identity from the
    already confirmed task, Beijing trigger time, and registered entry mode.
    This is an execution identity adapter, not a business fact or historical
    fallback; callers must never use an old request to fill the value.
    """
    supplied = str(request_id or "").strip()
    if supplied:
        return supplied
    task = str(task_id or "").strip()
    stamp = str(requested_at_beijing or "").strip()
    mode = str(entry_mode or "").strip().upper()
    if not task or not stamp or not mode:
        raise ValueError("request identity derivation requires task_id, requested_at_beijing and entry_mode")
    digest = hashlib.sha256(f"{task}|{stamp}|{mode}".encode("utf-8")).hexdigest()[:12]
    prefix = "scheduled-system-review" if mode == "AUTOMATIC_SCHEDULED_TASK" else "manual-system-review"
    stamp_slug = re.sub(r"[^0-9]+", "", stamp)[:14] or "runtime"
    return f"{prefix}-{stamp_slug}-{digest}"


def derive_scheduled_review_identity(task_id: str, requested_at_beijing: str,
                                     entry_mode: str, request_id: str = "",
                                     task_run_id: str = "", occurrence_id: str = "") -> tuple[str, str, str]:
    """Derive the complete shared Scheduled Review identity without history."""
    request = derive_request_identity(task_id, requested_at_beijing, entry_mode, request_id)
    run, occurrence = derive_runtime_identity(
        task_id, request, requested_at_beijing, entry_mode, task_run_id, occurrence_id
    )
    return request, run, occurrence
