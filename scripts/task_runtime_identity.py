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
