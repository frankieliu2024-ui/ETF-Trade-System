from __future__ import annotations

from typing import Any


INFLIGHT_STATUSES = {"BUILDING", "PENDING", "QUEUED", "RUNNING", "IN_PROGRESS", "IN_FLIGHT"}
READY_STATUSES = {"READY", "RESOLVED_DEGRADED"}
TERMINAL_FAILURE_STATUSES = {"FAILED", "TERMINAL_FAILURE", "BLOCKED_TERMINAL"}
BUSINESS_REPLY_OUTPUT = "COMPLETE_BUSINESS_DECISION_ONLY"
POST_REPLY_ASYNC = "CANONICAL_AUDIT_NOTIFICATION_ASYNC"
ACTOR_CONTINUE = "CONTINUE_SAME_REQUEST_SILENTLY"
ACTOR_DECIDE = "COMPLETE_BUSINESS_DECISION_NOW"
ACTOR_TERMINATE = "TERMINATE_ON_EXPLICIT_FAILURE"


def classify_formal_request_consumer_state(
    parent_request_id: str,
    query_context: dict[str, Any] | None,
    *,
    producer_status: str = "",
    terminal_failure: bool = False,
    canonical_persistence_status: str = "",
) -> dict[str, Any]:
    """Classify a durable Formal Decision parent without inventing a second gate.

    The request-bound formal_reply_freeze is the reply decision boundary.
    Global e2e_status and close-review projections are observability or
    post-decision state and must not override a same-request READY gate.
    This helper is read-only and interprets the existing request-bound
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
            "actor_action": ACTOR_TERMINATE,
            "user_output_allowed": True,
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

    # A caller-side status is not proof of a terminal state.  In particular,
    # stale/empty derived context, a connector empty read, and an in-flight
    # producer must remain WAIT.  A producer status without a request-bound
    # context is not attributable to this parent and may belong to an older
    # request; it must never terminate the current parent.
    # TERMINAL is fail-closed: it requires an explicit terminal status from the
    # current request-bound freeze, or a producer terminal status while the
    # current parent is already bound in the same context.
    context_bound_to_parent = bool(bound_request and bound_request == parent)
    explicit_terminal = context_bound_to_parent and (
        freeze_status in TERMINAL_FAILURE_STATUSES
        or producer in TERMINAL_FAILURE_STATUSES
    )
    if explicit_terminal:
        return {
            "status": "TERMINAL_FAILURE",
            "continue_same_request": False,
            "analysis_eligible": False,
            "reply_eligible": False,
            "user_visible_output": "TERMINAL_FAILURE",
            "actor_action": ACTOR_TERMINATE,
            "user_output_allowed": True,
            "reason": "EXPLICIT_TERMINAL_FAILURE_STATUS",
        }

    same_request = bound_request == parent and (not freeze_request or freeze_request == parent)
    dwp = pack.get("decision_work_package")
    dwp = dwp if isinstance(dwp, dict) else {}
    graph = dwp.get("problem_graph")
    graph = graph if isinstance(graph, list) else []
    required_problem_ids = [
        str(item.get("problem_id") or "").strip()
        for item in graph
        if isinstance(item, dict) and str(item.get("problem_id") or "").strip()
    ]
    # READY is valid only when the same request has a complete, non-empty
    # request-bound work package.  A stale gate, an empty connector read, or a
    # projection containing only status metadata must remain WAIT.  This is a
    # consumer boundary, not a second readiness algorithm.
    dwp_complete = bool(
        same_request
        and isinstance(context, dict)
        and pack
        and isinstance(pack.get("trigger"), dict)
        and bound_request == parent
        and isinstance(dwp, dict)
        and required_problem_ids
        and isinstance(dwp.get("response_contract"), dict)
    )
    if dwp_complete and freeze_status in READY_STATUSES and reply_freezable:
        # READY authorizes business judgment, but the actor must consume the
        # complete request-bound DWP before presenting the first business
        # report.  Expose the deterministic checklist here; this is not a new
        # state/checker and does not wait for persistence.
        response_contract = dwp.get("response_contract") if isinstance(dwp, dict) else {}
        response_contract = response_contract if isinstance(response_contract, dict) else {}
        # Request-bound facts authorize business analysis.  The actor's
        # BUSINESS_DECISION_SOURCE is the reply handoff; canonical projection
        # and post-write acceptance remain asynchronous and must not block the
        # user-visible business answer.
        return {
            "status": "BUSINESS_DECISION_READY",
            "continue_same_request": False,
            "analysis_eligible": True,
            "reply_eligible": True,
            "user_visible_output": BUSINESS_REPLY_OUTPUT,
            "actor_action": ACTOR_DECIDE,
            "user_output_allowed": True,
            "post_reply_processing": POST_REPLY_ASYNC,
            "required_problem_ids": required_problem_ids,
            "required_problem_count": len(required_problem_ids),
            "business_answer_contract": "DWP_EXACT_COVERAGE_BEFORE_PRESENTATION",
            "actor_response_contract": response_contract,
            "control_plane_in_user_reply": False,
            "reason": (
                "SAME_REQUEST_FACTS_READY_BDS_HANDOFF_REQUIRED_"
                "CANONICAL_PERSISTENCE_ASYNC"
            ),
            "canonical_persistence_status": (
                "CONFIRMED" if canonical_persisted else "PENDING_ASYNC"
            ),
            # This is an asynchronous post-reply handoff contract, not a
            # user-visible gate, new state, or producer.  The Actor freezes
            # FINAL_CONTENT and returns it first; this payload is then consumed
            # once by the existing BDS path using the exact same content.
            "ready_handoff": {
                "required": True,
                "submission_type": "BUSINESS_DECISION_SOURCE",
                "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
                "parent_request_id": parent,
                "required_payload_fields": [
                    "request_id",
                    "parent_request_id",
                    "entry_mode",
                    "source",
                    "task_id",
                    "task_run_id",
                    "occurrence_id",
                    "market_date",
                    "decision_response.answers",
                    "decision_id",
                    "consumed_snapshot",
                    "presentation_content",
                    "content_hash",
                    "target_path",
                ],
                "target_path": (
                    "requests/live_snapshot/"
                    + parent
                    + "__business_decision_source.json"
                ),
                "binding_rules": {
                    "parent_request_id": parent,
                    "allowed_sources": [
                        "CHATGPT_BUSINESS_DECISION_RESPONSE",
                    ],
                    "forbid_historical_fallback": True,
                    "read_back_required": True,
                    "formal_decision_notification_chain_starts_at_bds": True,
                    "completion_and_report_not_required_for_formal_decision_notification": True,
                    "bds_must_be_written_and_read_back_before_user_reply": True,
                },
                "user_reply_precondition": False,
                "notification_attempt_precondition": True,
                "provider_success_not_required_for_user_reply": True,
                "user_reply_contract": {
                    "must_be_first_user_visible_message": True,
                    "must_contain_complete_final_content": True,
                    "status_only_reply_forbidden": True,
                    "handoff_status_cannot_replace_content": True,
                    "bds_must_reuse_exact_final_content": True,
                },
                "post_reply_async": True,
                "must_read_back_after_reply": True,
                "historical_fallback_forbidden": True,
            },
        }

    if not same_request:
        return {
            "status": "REQUEST_BOUND_FACTS_BUILDING",
            "continue_same_request": True,
            "analysis_eligible": False,
            "reply_eligible": False,
            "user_visible_output": "SILENT_CONTINUATION",
            "actor_action": ACTOR_CONTINUE,
            "user_output_allowed": False,
            "reason": "CURRENT_DERIVED_CONTEXT_NOT_YET_BOUND_TO_DURABLE_PARENT",
        }

    if freeze_status in INFLIGHT_STATUSES or producer in INFLIGHT_STATUSES or not freeze_status:
        return {
            "status": "REQUEST_BOUND_FACTS_BUILDING",
            "continue_same_request": True,
            "analysis_eligible": False,
            "reply_eligible": False,
            "user_visible_output": "SILENT_CONTINUATION",
            "actor_action": ACTOR_CONTINUE,
            "user_output_allowed": False,
            "reason": "SAME_REQUEST_FACTS_STILL_FORMING",
        }

    return {
        "status": "REQUEST_BOUND_FACTS_BUILDING",
        "continue_same_request": True,
        "analysis_eligible": False,
        "reply_eligible": False,
        "user_visible_output": "SILENT_CONTINUATION",
        "actor_action": ACTOR_CONTINUE,
        "user_output_allowed": False,
        "reason": "NO_EXPLICIT_TERMINAL_FAILURE_AND_REPLY_NOT_YET_READY",
    }
