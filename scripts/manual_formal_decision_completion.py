from __future__ import annotations

import argparse
import json
import hashlib
import os
import tempfile
from pathlib import Path

try:
    from runtime_session_gate import classify_live_snapshot_request, interaction_scenario_for_time, parse_runtime_time
except ModuleNotFoundError:
    from scripts.runtime_session_gate import classify_live_snapshot_request, interaction_scenario_for_time, parse_runtime_time

try:
    from business_decision_source import _normalize_formal_presentation_layout, project_decision_response, validate_source
except ModuleNotFoundError:
    from scripts.business_decision_source import _normalize_formal_presentation_layout, project_decision_response, validate_source

try:
    from user_visible_presentation import build_formal_decision_presentation_binding
except ModuleNotFoundError:
    from scripts.user_visible_presentation import build_formal_decision_presentation_binding

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
REQUEST_DIR = ROOT / "requests" / "live_snapshot"


def _safe_id(value: object) -> str:
    value = str(value or "").strip()
    if not value or any(part in value for part in ("/", "\\", "..")):
        raise ValueError("manual completion requires a safe request identity")
    return value


def _source_interaction_scenario(source_request: dict) -> str:
    """Resolve the scenario without requiring normalized runtime-only fields to be durable."""
    supplied = str(source_request.get("interaction_scenario") or source_request.get("scenario") or "").strip().upper()
    if supplied:
        return supplied
    requested = parse_runtime_time(
        source_request.get("requested_at_beijing")
        or source_request.get("request_time_beijing")
        or source_request.get("requested_at_utc")
    )
    if requested is None:
        raise ValueError("source request is missing a valid request time for interaction scenario")
    policy_path = ROOT / "config" / "runtime_policy.json"
    if not policy_path.exists():
        raise ValueError("runtime policy is required to derive source interaction scenario")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    return interaction_scenario_for_time(policy, requested)


def build_completion_request(source_request: dict, formal_decision: dict, consumed_snapshot: str, completion_request_id: str = "", request_bound_pit_closure: dict | None = None, observation_eligibility_closure: dict | None = None) -> dict:
    """Transport an already-formed manual conclusion through the existing state-sync request path."""
    if not isinstance(source_request, dict) or not source_request:
        raise ValueError("source manual request is required")
    source = str(source_request.get("source") or "").strip().upper()
    intent = str(source_request.get("intent") or source_request.get("query_intent") or "").strip().upper()
    manual_sources = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "CHATGPT_USER_CONTINUE",
        "CHATGPT_USER_GITHUB_INTRADAY",
        "CHATGPT_USER_GITHUB_DECISION",
        "CHATGPT_USER_REQUEST",
    }
    formal_intents = {"EXPLICIT_LATEST", "FORMAL_INTRADAY_ANALYSIS"}
    if source not in manual_sources or intent not in formal_intents:
        raise ValueError("source request is not a manual formal analysis")
    if not isinstance(formal_decision, dict) or not formal_decision:
        raise ValueError("completed formal_decision payload is required")
    parent_id = _safe_id(source_request.get("request_id"))
    snapshot = str(consumed_snapshot or source_request.get("consumed_snapshot") or formal_decision.get("consumed_snapshot") or "").strip()
    if not snapshot:
        raise ValueError("manual completion requires an explicit consumed PIT snapshot")
    envelope_id = _safe_id(completion_request_id or f"{parent_id}__formal_completion")
    if envelope_id == parent_id:
        raise ValueError("completion envelope identity must differ from its parent request")
    scenario = _source_interaction_scenario(source_request)
    for closure_name, closure in (
        ("request_bound_pit_closure", request_bound_pit_closure),
        ("observation_eligibility_closure", observation_eligibility_closure),
    ):
        if closure is not None:
            if not isinstance(closure, dict):
                raise ValueError(f"{closure_name} must be an object")
            closure_parent = str(closure.get("parent_request_id") or "").strip()
            if closure_parent != parent_id:
                raise ValueError(f"{closure_name} parent_request_id must match source request")

    payload = {
        "request_id": envelope_id,
        "parent_request_id": parent_id,
        "request_type": "STATE_SYNC_ONLY",
        "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
        "interaction_scenario": scenario,
        "requested_at_beijing": source_request.get("requested_at_beijing") or source_request.get("request_time_beijing"),
        "market_date": source_request.get("market_date"),
        "persistence_available_at_beijing": source_request.get("persistence_available_at_beijing") or source_request.get("requested_at_beijing") or source_request.get("request_time_beijing"),
        "consumed_snapshot": snapshot,
        "formal_decision": formal_decision,
        "request_bound_pit_closure": request_bound_pit_closure,
        "observation_eligibility_closure": observation_eligibility_closure,
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    if classify_live_snapshot_request(payload) != "STATE_SYNC_ONLY":
        raise ValueError("manual completion must remain STATE_SYNC_ONLY")
    return payload


def _hydrate_observation_thesis_from_registry(work_package: dict) -> dict:
    """Compatibility projection for legacy/current work packages.

    The durable observation-thesis registry is the canonical source for an
    already admitted observation ETF. If a request-bound work package omits
    that state, fill only missing fields from the registry; never invent or
    overwrite supplied values. A missing/incomplete registry entry remains a
    hard validation failure in the canonical business-decision contract.
    """
    projected = json.loads(json.dumps(work_package, ensure_ascii=False))
    registry_path = ROOT / "config" / "market" / "etf_observation_thesis_registry.json"
    if not registry_path.exists():
        return projected
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    objects = registry.get("objects") if isinstance(registry, dict) else {}
    if not isinstance(objects, dict):
        return projected
    required = ("name", "thscode", "thesis", "falsifier", "next_decision_information", "information_value_reason")
    for item in projected.get("problem_graph") or []:
        if not isinstance(item, dict):
            continue
        problem_id = str(item.get("problem_id") or "")
        if not problem_id.startswith("OBSERVATION:"):
            continue
        code = problem_id.split(":", 1)[1].strip()
        canonical = objects.get(code)
        if not isinstance(canonical, dict):
            continue
        existing = item.get("existing_thesis_state")
        if not isinstance(existing, dict):
            existing = {}
            item["existing_thesis_state"] = existing
        for field in required:
            if not str(existing.get(field) or "").strip() and str(canonical.get(field) or "").strip():
                existing[field] = canonical[field]
    return projected


def build_structured_completion_request(
    source_request: dict,
    formal_decision: dict | None,
    decision_response: dict,
    decision_work_package: dict,
    consumed_snapshot: str,
    completion_request_id: str = "",
    request_bound_pit_closure: dict | None = None,
    observation_eligibility_closure: dict | None = None,
    presentation_content: str = "",
) -> dict:
    """Build the production actor envelope without canonical Formal Decision nesting."""
    if not isinstance(source_request, dict) or not source_request:
        raise ValueError("source Formal Decision request is required")
    decision_work_package = _hydrate_observation_thesis_from_registry(decision_work_package)
    parent_id = _safe_id(source_request.get("request_id"))
    scenario = _source_interaction_scenario(source_request)
    if not isinstance(decision_response, dict) or not decision_response.get("answers"):
        raise ValueError("new formal decision completion requires structured decision_response answers")
    if not isinstance(decision_work_package, dict) or not decision_work_package.get("problem_graph"):
        raise ValueError("new formal decision completion requires request-bound decision_work_package")
    snapshot = str(consumed_snapshot or source_request.get("consumed_snapshot") or "").strip()
    if not snapshot:
        raise ValueError("new formal decision completion requires an explicit consumed PIT snapshot")
    envelope_id = _safe_id(completion_request_id or f"{parent_id}__business_decision_source")
    decision_id = str(
        (formal_decision or {}).get("decision_id")
        or source_request.get("decision_id")
        or f"{parent_id}__decision"
    ).strip()
    if not decision_id:
        raise ValueError("new formal decision completion requires a decision identity")


    source_mode = str(source_request.get("source") or "").strip().upper()
    entry_mode = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS": "MANUAL_CONVERSATION",
        "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS": "MANUAL_SCHEDULED_TASK",
        "CHATGPT_SCHEDULED_FORMAL_ANALYSIS": "AUTOMATIC_SCHEDULED_TASK",
    }.get(source_mode)
    if not entry_mode:
        raise ValueError("unsupported Formal Decision source identity")

    # Preflight with the exact child-envelope identity and ingress metadata
    # that will be persisted below.  The old preflight omitted the entry
    # fields and used a parent/child pair that the canonical validator
    # correctly rejected.
    preflight_source = {
        "request_type": "BUSINESS_DECISION_SOURCE",
        "request_id": envelope_id,
        "parent_request_id": parent_id,
        "formal_entry_source": source_mode,
        "entry_mode": entry_mode,
        "task_id": source_request.get("task_id", ""),
        "task_run_id": source_request.get("task_run_id", ""),
        "occurrence_id": source_request.get("occurrence_id", ""),
        "market_date": source_request.get("market_date", ""),
        "decision_id": decision_id,
        "consumed_snapshot": snapshot,
    }
    projected = project_decision_response(preflight_source, decision_response, decision_work_package)
    validate_source(projected, expected_snapshot=snapshot)
    frozen_content = presentation_content if presentation_content not in (None, "") else None
    if frozen_content is None and "presentation_content" in decision_response:
        frozen_content = decision_response.get("presentation_content")
    if frozen_content not in (None, "") and not isinstance(frozen_content, str):
        raise ValueError("presentation_content must be a string")
    if isinstance(frozen_content, str) and not frozen_content.strip():
        raise ValueError("presentation_content must not be empty")
    if isinstance(frozen_content, str):
        # Canonicalize once before BDS and completion are written. The same
        # bytes must be reused by the user response, binding, hashes and
        # downstream event; no later stage may rewrite the body.
        frozen_content = _normalize_formal_presentation_layout(frozen_content)
    response_payload = dict(decision_response)
    if frozen_content not in (None, ""):
        # The report shown to the user and the report handed to the canonical
        # producer must be the same frozen bytes.  The downstream consumer
        # owns construction of presentation_binding; the actor envelope only
        # carries the immutable content once, plus this mirrored field for
        # same-source consistency validation.
        response_payload["presentation_content"] = frozen_content
    source_mode = str(source_request.get("source") or "").strip().upper()
    entry_mode = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS": "MANUAL_CONVERSATION",
        "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS": "MANUAL_SCHEDULED_TASK",
        "CHATGPT_SCHEDULED_FORMAL_ANALYSIS": "AUTOMATIC_SCHEDULED_TASK",
    }.get(source_mode)
    if not entry_mode:
        raise ValueError("unsupported Formal Decision source identity")
    payload = {
        "request_id": envelope_id,
        "parent_request_id": parent_id,
        "request_type": "BUSINESS_DECISION_SOURCE",
        "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
        "interaction_scenario": scenario,
        "formal_entry_source": source_mode,
        "entry_mode": entry_mode,
        "task_id": source_request.get("task_id", ""),
        "task_run_id": source_request.get("task_run_id", ""),
        "occurrence_id": source_request.get("occurrence_id", ""),
        "requested_at_beijing": source_request.get("requested_at_beijing") or source_request.get("request_time_beijing"),
        "market_date": source_request.get("market_date"),
        "consumed_snapshot": snapshot,
        "decision_id": decision_id,
        "decision_response": response_payload,
        "decision_work_package": decision_work_package,
        "presentation_content": frozen_content,
        "content_hash": hashlib.sha256(frozen_content.encode("utf-8")).hexdigest() if isinstance(frozen_content, str) else "",
        "presentation_binding": build_formal_decision_presentation_binding(
            frozen_content,
            request_id=envelope_id,
            decision_id=decision_id,
            parent_request_id=parent_id,
            report_id=f"etf-formal-decision:{decision_id}",
        ) if isinstance(frozen_content, str) else {},
        "target_bds_path": f"requests/live_snapshot/{parent_id}__business_decision_source.json",
        "request_bound_pit_closure": request_bound_pit_closure,
        "observation_eligibility_closure": observation_eligibility_closure,
    }
    if frozen_content not in (None, ""):
        payload["presentation_content"] = frozen_content

    # Validate the final persisted envelope after the frozen presentation is
    # attached. The preflight above intentionally has no presentation body;
    # validating only that preflight allowed stale FINAL_CONTENT to enter a new
    # request. This second validation binds the actual body to this request,
    # requested_at_beijing, decision identity, snapshot and presentation hash.
    validate_source(payload, expected_snapshot=snapshot)
    return {key: value for key, value in payload.items() if value not in (None, "")}


def build_formal_decision_completion_request(
    source_request: dict,
    bds_path: str,
    decision_id: str,
    content_hash: str,
    completion_request_id: str = "",
    bds_payload: dict | None = None,
) -> dict:
    """Compatibility facade delegating Formal completion to the shared owner."""
    try:
        from scheduled_review_completion import build_formal_decision_completion_request as shared_builder
    except ModuleNotFoundError:
        from scripts.scheduled_review_completion import build_formal_decision_completion_request as shared_builder
    return shared_builder(
        source_request,
        bds_path,
        decision_id,
        content_hash,
        completion_request_id=completion_request_id,
        bds_payload=bds_payload,
    )

def build_ready_actor_envelope(
    source_request: dict,
    formal_decision: dict | None,
    decision_response: dict,
    decision_work_package: dict,
    consumed_snapshot: str,
    final_content: str,
    completion_request_id: str = "",
) -> dict:
    """Build the one-shot handoff produced immediately after a READY reply.

    This is intentionally a builder, not a second producer.  It requires the
    exact frozen user-facing report and binds it to the same structured
    answers, decision identity, DWP and snapshot used by the BDS projector.
    Missing content fails closed instead of allowing a later interaction to
    regenerate or guess the report.
    """
    if not isinstance(final_content, str) or not final_content.strip():
        raise ValueError("READY actor handoff requires the frozen presentation_content")
    response_payload = dict(decision_response or {})
    response_payload["presentation_content"] = final_content
    payload = build_structured_completion_request(
        source_request,
        formal_decision,
        response_payload,
        decision_work_package,
        consumed_snapshot,
        completion_request_id=completion_request_id,
        presentation_content=final_content,
    )
    if payload.get("presentation_content") != final_content:
        raise ValueError("READY actor handoff presentation_content was not preserved")
    if payload.get("decision_response", {}).get("presentation_content") != final_content:
        raise ValueError("READY actor handoff response content is not source-bound")
    return payload



def write_completion_from_persisted_bds(
    source_request_path: str,
    bds_path: str,
    consumed_snapshot: str = "",
    output_path: str = "",
    completion_request_id: str = "",
) -> Path:
    """Continue the canonical handoff from one already persisted BDS.

    The persisted BDS is the authoritative request-bound handoff for this
    recovery path. Do not re-read the mutable global query_context here:
    doing so can reject a valid BDS when the shared fact projection has moved
    to a later request. This adapter validates the BDS identity and delegates
    completion-envelope construction to the canonical builder.
    """
    source_path = (ROOT / source_request_path).resolve()
    bds_file = (ROOT / bds_path).resolve()
    if ROOT not in source_path.parents or ROOT not in bds_file.parents:
        raise ValueError("source and BDS paths must stay inside repository root")
    if not source_path.exists() or not bds_file.exists():
        raise ValueError("source request and persisted BDS are required")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    bds = json.loads(bds_file.read_text(encoding="utf-8"))
    parent_id = str(source.get("request_id") or "").strip()
    if not parent_id:
        raise ValueError("source request identity is required")
    if str(bds.get("parent_request_id") or "").strip() != parent_id:
        raise ValueError("persisted BDS parent_request_id does not match source request")
    expected_bds_id = f"{parent_id}__business_decision_source"
    if str(bds.get("request_id") or "").strip() != expected_bds_id:
        raise ValueError("persisted BDS request_id is not the canonical child identity")
    decision_response = bds.get("decision_response")
    work_package = bds.get("decision_work_package")
    if not isinstance(decision_response, dict) or not decision_response:
        raise ValueError("persisted BDS decision_response is required")
    if not isinstance(work_package, dict) or not work_package.get("problem_graph"):
        # Compatibility recovery for BDS written by the older projector: use
        # only the current query context when its trigger is the same parent.
        query_path = ROOT / "data" / "state" / "query_context.json"
        if not query_path.exists():
            raise ValueError("persisted BDS decision_work_package is required")
        query = json.loads(query_path.read_text(encoding="utf-8"))
        packet = query.get("decision_fact_pack") or {}
        trigger = packet.get("trigger") or {}
        if str(trigger.get("request_id") or "").strip() != parent_id:
            raise ValueError("persisted BDS decision_work_package is missing and current query context is not same-parent")
        candidate = packet.get("decision_work_package")
        if not isinstance(candidate, dict) or not candidate.get("problem_graph"):
            raise ValueError("persisted BDS decision_work_package is required")
        work_package = candidate
        bds["decision_work_package"] = work_package
    final_content = str(
        bds.get("presentation_content")
        or decision_response.get("presentation_content")
        or ""
    )
    if not final_content.strip():
        raise ValueError("persisted BDS frozen presentation_content is required")
    snapshot = str(consumed_snapshot or bds.get("consumed_snapshot") or "").strip()
    if not snapshot:
        raise ValueError("persisted BDS consumed_snapshot is required")
    if str(bds.get("consumed_snapshot") or "").strip() != snapshot:
        raise ValueError("persisted BDS consumed_snapshot does not match requested snapshot")
    decision_id = str(bds.get("decision_id") or "").strip()
    content_hash = str(bds.get("content_hash") or "").strip()
    if not decision_id or not content_hash:
        raise ValueError("persisted BDS decision_id and content_hash are required")
    completion_payload = build_formal_decision_completion_request(
        source,
        str(bds_file.relative_to(ROOT)).replace("\\\\", "/"),
        decision_id,
        content_hash,
        completion_request_id=completion_request_id,
        bds_payload=bds,
    )
    target = (ROOT / output_path).resolve() if output_path else REQUEST_DIR / f"{completion_payload['request_id']}.json"
    if ROOT not in target.parents or target.suffix != ".json":
        raise ValueError("completion output path must be a repository JSON path")
    if target.name != f"{completion_payload['request_id']}.json":
        raise ValueError("completion filename must equal its envelope request_id")
    serialized = json.dumps(completion_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != serialized:
            raise FileExistsError("completion path already contains a different payload")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized, encoding="utf-8")
    return target

def write_completion_request(source_request_path: str, decision_path: str, consumed_snapshot: str, output_path: str = "", completion_request_id: str = "", decision_response_path: str = "", final_content_path: str = "") -> Path:
    source_path = (ROOT / source_request_path).resolve()
    decision_file = (ROOT / decision_path).resolve() if decision_path else None
    if ROOT not in source_path.parents or (decision_file is not None and ROOT not in decision_file.parents):
        raise ValueError("input paths must stay inside repository root")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_file.read_text(encoding="utf-8")) if decision_file is not None and decision_file.exists() else {}
    query_path = ROOT / "data/state/query_context.json"
    pit_closure = None
    observation_closure = None
    if query_path.exists():
        query = json.loads(query_path.read_text(encoding="utf-8"))
        packet = query.get("decision_fact_pack") or {}
        trigger = packet.get("trigger") or {}
        action = packet.get("formal_action_readiness") or {}
        freshness = query.get("freshness_assurance") or {}
        if (
            str(trigger.get("request_id") or "").strip() == str(source.get("request_id") or "").strip()
            and action.get("ready") is True
            and str(action.get("status") or "").upper() == "READY"
            and action.get("request_scoped_pit_resolved") is True
        ):
            pit_closure = {
                "parent_request_id": str(source.get("request_id") or "").strip(),
                "status": "READY",
                "request_scoped_pit_resolved": True,
                "decision_request_time": freshness.get("decision_request_time"),
                "refresh_mode": freshness.get("refresh_mode"),
                "freshness_status": freshness.get("status"),
                "query_context_generated_at_beijing": query.get("generated_at_beijing"),
            }
        discovery = query.get("formal_etf_discovery") or {}
        candidates = discovery.get("candidates") or []
        if (
            str(trigger.get("request_id") or "").strip() == str(source.get("request_id") or "").strip()
            and str(discovery.get("status") or "").upper() in {"READY", "COMPLETE"}
            and isinstance(candidates, list)
        ):
            observation_closure = {
                "parent_request_id": str(source.get("request_id") or "").strip(),
                "status": "READY",
                "query_context_generated_at_beijing": query.get("generated_at_beijing"),
                "candidates": [
                    {
                        "code": str(item.get("code") or "").strip(),
                        "formal_quote_status": str(item.get("formal_quote_status") or "").upper(),
                    }
                    for item in candidates
                    if isinstance(item, dict) and str(item.get("code") or "").strip()
                ],
            }
    if not decision_response_path:
        raise ValueError("new Formal Decision production completion requires --decision-response")
    response_file = (ROOT / decision_response_path).resolve()
    if ROOT not in response_file.parents or not response_file.exists():
        raise ValueError("decision response path must stay inside repository root and exist")
    decision_response = json.loads(response_file.read_text(encoding="utf-8"))
    if not final_content_path:
        raise ValueError("Formal Decision production requires --final-content")
    final_file = (ROOT / final_content_path).resolve()
    if ROOT not in final_file.parents or not final_file.exists():
        raise ValueError("final content path must stay inside repository root and exist")
    final_content = final_file.read_text(encoding="utf-8")
    if not final_content.strip():
        raise ValueError("final content must not be empty")
    supplied_content = decision_response.get("presentation_content")
    if supplied_content not in (None, "") and supplied_content != final_content:
        raise ValueError("final content does not match decision response presentation_content")
    decision_response["presentation_content"] = final_content
    if not query_path.exists():
        raise ValueError("request-bound query context is required for structured Formal Decision completion")
    query = json.loads(query_path.read_text(encoding="utf-8"))
    packet = query.get("decision_fact_pack") or {}
    trigger = packet.get("trigger") or {}
    if str(trigger.get("request_id") or "").strip() != str(source.get("request_id") or "").strip():
        raise ValueError("decision work package is not bound to the source Formal Decision request")
    work_package = packet.get("decision_work_package") or {}
    payload = build_structured_completion_request(
        source, decision, decision_response, work_package, consumed_snapshot,
        completion_request_id, pit_closure, observation_closure,
        presentation_content=final_content,
    )
    target = (ROOT / output_path).resolve() if output_path else REQUEST_DIR / f"{payload['request_id']}.json"
    if ROOT not in target.parents or target.suffix != ".json":
        raise ValueError("output path must be a repository JSON path")
    expected_name = f"{payload['request_id']}.json"
    if target.name != expected_name:
        raise ValueError("Formal Decision completion filename must equal its envelope request_id")
    if payload.get("request_id") == payload.get("parent_request_id"):
        raise ValueError("Formal Decision completion envelope and parent identities must differ")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") == serialized:
            pass
        else:
            raise FileExistsError("completion request path already contains a different payload")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized, encoding="utf-8")
    # The BDS is only the first durable handoff.  Production must immediately
    # create the shared Scheduled Review completion from the exact persisted
    # BDS, otherwise notification projection has no legal completion ingress.
    bds_relative = str(target.relative_to(ROOT)).replace("\\", "/")
    persisted_bds = json.loads(target.read_text(encoding="utf-8"))
    completion_payload = build_formal_decision_completion_request(
        source,
        bds_relative,
        str(persisted_bds.get("decision_id") or decision.get("decision_id") or "").strip(),
        str(persisted_bds.get("content_hash") or "").strip(),
        bds_payload=persisted_bds,
    )
    completion_target = REQUEST_DIR / f"{completion_payload['request_id']}.json"
    completion_serialized = json.dumps(completion_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if completion_target.exists():
        if completion_target.read_text(encoding="utf-8") != completion_serialized:
            raise FileExistsError("Formal Decision completion path already contains a different payload")
    else:
        completion_target.write_text(completion_serialized, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-request", default="")
    parser.add_argument("--formal-decision", default="", help="historical compatibility only; new production source is projected from --decision-response")
    parser.add_argument("--consumed-snapshot", default="")
    parser.add_argument("--completion-request-id", default="")
    parser.add_argument("--decision-response", default="")
    parser.add_argument("--final-content", default="", help="path to the exact frozen FINAL_CONTENT from this run")
    parser.add_argument("--output", default="")
    parser.add_argument("--from-bds", default="", help="continue the canonical handoff from one persisted BUSINESS_DECISION_SOURCE")
    args = parser.parse_args()
    if args.from_bds:
        if not args.source_request:
            raise ValueError("--source-request is required with --from-bds")
        target = write_completion_from_persisted_bds(
            args.source_request, args.from_bds, args.consumed_snapshot,
            output_path=args.output, completion_request_id=args.completion_request_id,
        )
    else:
        if not args.source_request or not args.consumed_snapshot or not args.decision_response or not args.final_content:
            raise ValueError("new Formal Decision production requires --source-request, --consumed-snapshot, --decision-response and --final-content")
        target = write_completion_request(
            args.source_request, args.formal_decision, args.consumed_snapshot,
            args.output, args.completion_request_id, args.decision_response,
            args.final_content,
        )
    written = json.loads(target.read_text(encoding="utf-8"))
    completion_path = target.parent / f"{written['parent_request_id']}__scheduled_review_completion.json"
    print(json.dumps({
        "ok": True,
        "request": str(target.relative_to(ROOT)).replace("\\", "/"),
        "completion_request_id": written.get("request_id"),
        "parent_request_id": written.get("parent_request_id"),
        "completion_request": str(completion_path.relative_to(ROOT)).replace("\\", "/"),
        "handoff_required": True,
        "handoff": "decision-notification.workflow_dispatch(formal_completion_path=<request>)",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

