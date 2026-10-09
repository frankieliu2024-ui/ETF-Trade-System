from __future__ import annotations

from typing import Any


INFLIGHT_STATUSES = {"BUILDING", "PENDING", "QUEUED", "RUNNING", "IN_PROGRESS", "IN_FLIGHT"}
READY_STATUSES = {"READY", "RESOLVED_DEGRADED"}
TERMINAL_FAILURE_STATUSES = {"FAILED", "TERMINAL_FAILURE", "BLOCKED_TERMINAL"}


def classify_formal_request_consumer_state(
    parent_request_id: str,
    query_context: dict[str, Any] | None,
    *,
    producer_status: str = "",
    terminal_failure: bool = False,
    canonical_persistence_status: str = "",
) -> dict[str, Any]:
    """Classify a durable Formal Decision parent without inventing a second gate.

    This helper is read-only. It interprets the existing request-bound
    query_context/formal_reply_freeze contract for interactive consumers.
    """

    parent = str(parent_request_id or "").strip()
    if not parent:
        return {
            "status": "TERMINAL_FAILURE",
            "continue_same_request": False,
            "analysis_eligible": False,
            "reply_eligible": False,
            "user_visible_output": "TERMINAL_FAILURE",
            "reason": "PARENT_REQUEST_ID_MISSING",
        }

    context = query_context if isinstance(query_context, dict) else {}
    pack = context.get("decision_fact_pack") if isinstance(context.get("decision_fact_pack"), dict) else {}
    trigger = pack.get("trigger") if isinstance(pack.get("trigger"), dict) else {}
    bound_request = str(trigger.get("request_id") or "").strip()
    # The request-bound fact pack is the authoritative reply contract.  The
    # top-level projection is global/derived and may lag or belong to another
    # request; it must never turn a current request into a false READY state.
    freeze = pack.get("formal_reply_freeze")
    if not isinstance(freeze, dict):
        projected_freeze = context.get("formal_reply_freeze")
        freeze = projected_freeze if isinstance(projected_freeze, dict) else {}
    freeze_status = str(freeze.get("status") or "").strip().upper()
    freeze_request = str(freeze.get("request_id") or bound_request).strip()
    reply_freezable = bool(freeze.get("reply_freezable"))
    producer = str(producer_status or "").strip().upper()
    persistence = str(canonical_persistence_status or "").strip().upper()
    canonical_persisted = persistence in {"PERSISTED", "COMMITTED", "ACCEPTED", "PASS"}

    if terminal_failure or producer in TERMINAL_FAILURE_STATUSES:
        return {
            "status": "TERMINAL_FAILURE",
            "continue_same_request": False,
            "analysis_eligible": False,
            "reply_eligible": False,
            "user_visible_output": "TERMINAL_FAILURE",
            "reason": "EXPLICIT_TERMINAL_FAILURE",
        }

    same_request = bound_request == parent and (not freeze_request or freeze_request == parent)
    if same_request and freeze_status in READY_STATUSES and reply_freezable:
        # Request-bound facts authorize business analysis.  The actor's
        # BUSINESS_DECISION_SOURCE is the reply handoff; canonical projection
        # and post-write acceptance remain asynchronous and must not block the
        # user-visible business answer.
        return {
            "status": "BUSINESS_DECISION_READY",
            "continue_same_request": False,
            "analysis_eligible": True,
            "reply_eligible": True,
            "user_visible_output": "COMPLETE_BUSINESS_DECISION_ONLY",
            "reason": (
                "SAME_REQUEST_FACTS_READY_BDS_HANDOFF_REQUIRED_"
                "CANONICAL_PERSISTENCE_ASYNC"
            ),
            "canonical_persistence_status": (
                "CONFIRMED" if canonical_persisted else "PENDING_ASYNC"
            ),
        }

    if not same_request:
        return {
            "status": "REQUEST_BOUND_FACTS_BUILDING",
            "continue_same_request": True,
            "analysis_eligible": False,
            "reply_eligible": False,
            "user_visible_output": "SILENT_CONTINUATION",
            "reason": "CURRENT_DERIVED_CONTEXT_NOT_YET_BOUND_TO_DURABLE_PARENT",
        }

    if freeze_status in INFLIGHT_STATUSES or producer in INFLIGHT_STATUSES or not freeze_status:
        return {
            "status": "REQUEST_BOUND_FACTS_BUILDING",
            "continue_same_request": True,
            "analysis_eligible": False,
            "reply_eligible": False,
            "user_visible_output": "SILENT_CONTINUATION",
            "reason": "SAME_REQUEST_FACTS_STILL_FORMING",
        }

    return {
        "status": "REQUEST_BOUND_FACTS_BUILDING",
        "continue_same_request": True,
        "analysis_eligible": False,
        "reply_eligible": False,
        "user_visible_output": "SILENT_CONTINUATION",
        "reason": "NO_EXPLICIT_TERMINAL_FAILURE_AND_REPLY_NOT_YET_READY",
    }
