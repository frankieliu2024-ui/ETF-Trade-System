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
    "decision_evidence_consumption",
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

# Ephemeral, opt-in forensic tracing for controlled historical Formal replay.
# This state exists only in process memory and never enters any business payload.
_FORENSIC_TRACE = {"enabled": False, "baseline_graph_sha256": "", "first_divergence_reported": False, "previous": None}

def reset_formal_replay_forensic_trace(enabled: bool = False) -> None:
    """Reset this process-local diagnostic trace; does not touch domain state."""
    _FORENSIC_TRACE.update({"enabled": bool(enabled), "baseline_graph_sha256": "", "first_divergence_reported": False, "previous": None})

def emit_formal_replay_forensic_fingerprint(
    stage: str,
    *,
    obj: Any = None,
    dwp: Any = None,
    object_type: str = "",
    source_type: str = "",
    provenance: str = "",
    source_fingerprint_value: str = "",
    historical: bool = False,
) -> None:
    """Write a redacted runtime fingerprint to stdout only when explicitly enabled."""
    if not _FORENSIC_TRACE["enabled"] or not historical:
        return
    dwp_obj = dwp if isinstance(dwp, dict) else None
    graph_present = bool(dwp_obj is not None and "problem_graph" in dwp_obj)
    graph = dwp_obj.get("problem_graph") if graph_present else None
    graph = graph if isinstance(graph, list) else []
    problem_ids = [str(item.get("problem_id") or "") for item in graph if isinstance(item, dict) and item.get("problem_id")]
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sha = lambda value: hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
    graph_sha = sha(graph) if graph_present else ""
    ids_sha = sha(problem_ids)
    dwp_sha = sha(dwp_obj) if dwp_obj is not None else ""
    previous = _FORENSIC_TRACE.get("previous")
    record = {
        "event": "FORMAL_REPLAY_FORENSIC_FINGERPRINT",
        "stage": stage,
        "problem_ids": problem_ids,
        "problem_ids_sha256": ids_sha,
        "problem_graph_sha256": graph_sha,
        "dwp_sha256": dwp_sha,
        "object_type": object_type or (type(obj).__name__ if obj is not None else "null"),
        "source_type": source_type or (str((obj or {}).get("source_type") or (obj or {}).get("request_type") or "") if isinstance(obj, dict) else ""),
        "provenance": provenance,
        "object_identity": {"object_id": hex(id(obj)) if obj is not None else "", "dwp_id": hex(id(dwp)) if dwp is not None else "", "graph_id": hex(id(graph)) if graph_present else ""},
        "mutation_evidence": {
            "previous_stage": previous.get("stage") if previous else "",
            "same_graph_content_as_previous": bool(previous and graph_sha and graph_sha == previous.get("problem_graph_sha256")),
            "same_dwp_object_as_previous": bool(previous and dwp is not None and hex(id(dwp)) == previous.get("dwp_id")),
            "graph_present_in_object": graph_present,
        },
        "source_fingerprint": source_fingerprint_value,
    }
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
    if stage == "A_HISTORICAL_PACKET" and graph_sha:
        _FORENSIC_TRACE["baseline_graph_sha256"] = graph_sha
    baseline = _FORENSIC_TRACE.get("baseline_graph_sha256")
    if stage != "A_HISTORICAL_PACKET" and graph_sha and baseline and graph_sha != baseline and not _FORENSIC_TRACE["first_divergence_reported"]:
        print(json.dumps({"event": "FIRST_DIVERGENCE_STAGE", "stage": stage, "historical_graph_sha256": baseline, "observed_graph_sha256": graph_sha}, sort_keys=True), flush=True)
        _FORENSIC_TRACE["first_divergence_reported"] = True
    _FORENSIC_TRACE["previous"] = {"stage": stage, "problem_graph_sha256": graph_sha, "dwp_id": hex(id(dwp)) if dwp is not None else ""}


_FORMAL_OPPORTUNITY_ALIASES = {
    "无机会": "无机会",
    "无新增交易机会": "无机会",
    "观察机会": "观察机会",
    "Trial机会": "Trial机会",
    "Confirm机会": "Confirm机会",
}

def _normalize_opportunity_status(value: Any) -> str:
    return _FORMAL_OPPORTUNITY_ALIASES.get(str(value or "").strip(), "")

_FORMAL_RISK_PERMISSION_VALUES = ("禁止新增", "允许Trial", "允许Confirm")

def _normalize_risk_permission(value: Any) -> str:
    """Project an actor risk-permission sentence onto the existing formal enum.

    The actor-facing DWP asks for a business judgment, so final_action may carry
    an explanatory suffix. Persistence owns only the leading registered
    permission. Ambiguous or unregistered text remains fail-closed.
    """
    text = str(value or "").strip()
    if text in _FORMAL_RISK_PERMISSION_VALUES:
        return text
    matches = [item for item in _FORMAL_RISK_PERMISSION_VALUES if text.startswith(item)]
    if len(matches) == 1:
        suffix = text[len(matches[0]):]
        if not suffix or suffix[0] in "；;，,。:： （(":
            return matches[0]
    return ""

_HOLDING_ACTION_ALIASES = {
    "HOLD": "HOLD",
    "持有": "HOLD",
    "持有管理": "HOLD",
    "持仓管理": "HOLD",
    "REDUCE": "REDUCE",
    "降低风险": "REDUCE",
    "EXIT": "EXIT",
    "退出": "EXIT",
    "全部退出": "EXIT",
}


def _normalize_holding_action(value: Any) -> str:
    raw = str(value or "").strip()
    return _HOLDING_ACTION_ALIASES.get(raw.upper(), _HOLDING_ACTION_ALIASES.get(raw, raw.upper()))


def _parse_yuan_amount(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    raw = value
    if isinstance(value, str):
        raw = value.strip().replace(",", "").replace("人民币", "")
        if raw.endswith("元"):
            raw = raw[:-1].strip()
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be numeric")
    return number


def source_fingerprint(source: dict[str, Any]) -> str:
    body = {k: v for k, v in source.items() if k not in {"fingerprint", "status", "projection_status"}}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

def validate_decision_evidence_consumption(value: Any, *, parent_request_id: str = "") -> str:
    """Validate explicit evidence consumption without making an investment judgment."""
    if not isinstance(value, dict):
        return "decision_evidence_consumption must be an object"
    required = (
        "request_id",
        "layer_1_external_cross_market",
        "layer_2_a_share_internal",
        "layer_3_etf_opportunity_capital",
        "discovery_to_capital_competition_consumed",
        "all_managed_positions_sell_chain_consumed",
        "held_etf_additional_capital_consumed",
        "next_unit_capital_use_consumed",
    )
    layer_keys = {
        "layer_1_external_cross_market",
        "layer_2_a_share_internal",
        "layer_3_etf_opportunity_capital",
    }
    missing = [
        key for key in required
        if key not in value or (key not in layer_keys and value[key] in (None, "", [], {}))
    ]
    if missing:
        return "decision_evidence_consumption missing: " + ",".join(missing)
    for key in layer_keys:
        if not isinstance(value.get(key), list):
            return f"decision_evidence_consumption.{key} must be a list"
    if parent_request_id and str(value.get("request_id") or "").strip() != parent_request_id:
        return "decision_evidence_consumption request_id must match parent request"
    for key in (
        "discovery_to_capital_competition_consumed",
        "all_managed_positions_sell_chain_consumed",
        "held_etf_additional_capital_consumed",
        "next_unit_capital_use_consumed",
    ):
        if value.get(key) is not True:
            return f"decision_evidence_consumption.{key} must be true"
    return ""


def _transition_attribution_error(answer: dict, *, problem_id: str, qualified_requirement_ids: set[str]) -> str:
    attribution = answer.get("state_transition_attribution")
    if not isinstance(attribution, dict):
        return f"decision response material state transition requires {problem_id}.state_transition_attribution"
    delta = str(attribution.get("material_evidence_delta") or "").strip()
    evidence_ids = attribution.get("evidence_requirement_ids")
    if not delta:
        return f"decision response material state transition requires {problem_id}.material_evidence_delta"
    if not isinstance(evidence_ids, list) or not evidence_ids:
        return f"decision response material state transition requires {problem_id}.evidence_requirement_ids"
    invalid = [str(x) for x in evidence_ids if str(x) not in qualified_requirement_ids]
    if invalid:
        return f"decision response material state transition cites unqualified evidence for {problem_id}: " + ",".join(invalid)
    return ""


def _validate_material_state_transitions(
    answers: dict[str, Any], work_package: dict[str, Any], *, risk_permission: str,
    candidate_code: str, opportunity_status: str, position_reviews: list[dict[str, Any]],
) -> None:
    baseline = work_package.get("previous_formal_decision_baseline") or {}
    if not isinstance(baseline, dict) or not baseline:
        return
    requirements = work_package.get("evidence_requirements") or []
    qualified_by_problem: dict[str, set[str]] = {}
    for item in requirements:
        if not isinstance(item, dict) or str(item.get("satisfaction") or "").upper() not in {"SATISFIED", "DEGRADED"}:
            continue
        pid = str(item.get("target_problem_id") or "")
        rid = str(item.get("requirement_id") or "")
        if pid and rid:
            qualified_by_problem.setdefault(pid, set()).add(rid)

    prior_risk = _normalize_risk_permission(baseline.get("risk_permission"))
    if prior_risk and risk_permission != prior_risk:
        error = _transition_attribution_error(answers.get("RISK_PERMISSION") or {}, problem_id="RISK_PERMISSION", qualified_requirement_ids=qualified_by_problem.get("RISK_PERMISSION", set()))
        if error:
            raise ValueError(error)

    prior_code = str(baseline.get("candidate_code") or "").strip()
    prior_status = _normalize_opportunity_status(baseline.get("opportunity_status"))
    if (prior_code and candidate_code != prior_code) or (prior_status and opportunity_status != prior_status):
        error = _transition_attribution_error(answers.get("MAIN_CANDIDATE") or {}, problem_id="MAIN_CANDIDATE", qualified_requirement_ids=qualified_by_problem.get("MAIN_CANDIDATE", set()))
        if error:
            raise ValueError(error)

    prior_holding = baseline.get("holding_actions") or {}
    if isinstance(prior_holding, dict):
        for review in position_reviews:
            code = str(review.get("security_code") or "")
            prior_action = _normalize_holding_action(prior_holding.get(code))
            current_action = _normalize_holding_action(review.get("current_action"))
            if prior_action and current_action and prior_action != current_action:
                pid = f"HOLDING:{code}"
                error = _transition_attribution_error(answers.get(pid) or {}, problem_id=pid, qualified_requirement_ids=qualified_by_problem.get(pid, set()))
                if error:
                    raise ValueError(error)


def _capital_competition_opportunity_reviews(reviews: list[dict[str, Any]], graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse distinct role reviews for one ETF into one capital state.

    An ETF can already be a persistent Observation and also surface in the
    node-local Discovery set. Keep both role-level reviews for audit, but count
    the instrument once in executable capital competition. Conflicting status
    judgments fail closed and must be reconciled by the decision actor.
    """
    roles_by_code: dict[str, set[str]] = {}
    for item in graph:
        problem_id = str(item.get("problem_id") or "")
        if problem_id.startswith("OBSERVATION:"):
            code = problem_id.split(":", 1)[1].strip()
            category = "OBSERVED_ETF"
        elif problem_id.startswith("DISCOVERY:"):
            code = problem_id.split(":", 1)[1].strip()
            category = "OBSERVATION_EVALUATION_INPUT"
        else:
            continue
        if code:
            roles_by_code.setdefault(code, set()).add(category)

    allowed_overlap = {"OBSERVED_ETF", "OBSERVATION_EVALUATION_INPUT"}
    result: list[dict[str, Any]] = []
    index_by_code: dict[str, int] = {}
    seen_roles: dict[str, set[str]] = {}
    for review in reviews:
        code = str(review.get("code") or review.get("security_code") or "").strip()
        category = str(review.get("category") or "").strip().upper()
        expected_roles = roles_by_code.get(code, set())
        if not code or category not in expected_roles:
            raise ValueError("capital opportunity review does not match its decision-work-package role")
        if category in seen_roles.setdefault(code, set()):
            raise ValueError("duplicate capital opportunity review role for " + code)
        seen_roles[code].add(category)

        if code not in index_by_code:
            item = dict(review)
            item["category"] = "OBSERVED_ETF" if allowed_overlap <= expected_roles else category
            index_by_code[code] = len(result)
            result.append(item)
            continue

        if expected_roles != allowed_overlap:
            raise ValueError("duplicate capital opportunity identity for " + code)
        existing = result[index_by_code[code]]
        if str(existing.get("opportunity_status") or "") != str(review.get("opportunity_status") or ""):
            raise ValueError("conflicting opportunity status across roles for " + code)
        existing["category"] = "OBSERVED_ETF"
        existing["conclusion"] = "；".join(
            value for value in (str(existing.get("conclusion") or "").strip(), str(review.get("conclusion") or "").strip()) if value
        )
        existing["reason"] = "；".join(
            value for value in (str(existing.get("reason") or "").strip(), str(review.get("reason") or "").strip()) if value
        )
    return result


def project_decision_response(source: dict[str, Any], response: dict[str, Any], work_package: dict[str, Any], *, forensic_historical_replay: bool = False) -> dict[str, Any]:
    """Project actor-only business answers into the canonical Business Decision Source."""
    if forensic_historical_replay:
        emit_formal_replay_forensic_fingerprint("E_PROJECT_ENTRY", obj=source, dwp=work_package, object_type="project_decision_response.source", provenance="historical_replay", historical=True)
        emit_formal_replay_forensic_fingerprint("F_SOURCE_INTERNAL_DWP", obj=source, dwp=source.get("decision_work_package"), object_type="project_decision_response.source", provenance="historical_replay", historical=True)
    if not isinstance(response, dict) or not isinstance(work_package, dict):
        raise ValueError("decision response/work package must be objects")
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("decision response requires answers keyed by problem_id")
    graph = [x for x in (work_package.get("problem_graph") or []) if isinstance(x, dict)]
    if forensic_historical_replay:
        emit_formal_replay_forensic_fingerprint("G_COMPLETENESS_VALIDATOR_GRAPH", obj=work_package, dwp={"problem_graph": graph}, object_type="completeness_validator.problem_graph", provenance="historical_replay", historical=True)
    required_ids = [str(x.get("problem_id") or "") for x in graph if x.get("problem_id")]
    missing = [pid for pid in required_ids if pid not in answers]
    if missing:
        raise ValueError("decision response missing problem_ids: " + ",".join(missing))

    by_id = {str(x.get("problem_id") or ""): x for x in graph}
    for pid in required_ids:
        answer = answers[pid]
        if not isinstance(answer, dict):
            raise ValueError(f"decision response answer must be an object: {pid}")
        for field in ("final_action", "capital_comparison", "next_change_condition", "evidence_decision_impact"):
            if answer.get(field) in (None, "", [], {}):
                raise ValueError(f"decision response missing {pid}.{field}")
        if pid.startswith("HOLDING:"):
            if not answer.get("capital_occupancy_reason") or not answer.get("higher_efficiency_alternative"):
                raise ValueError(f"decision response missing holding capital rationale: {pid}")
            if _normalize_holding_action(answer.get("final_action")) in {"REDUCE", "EXIT"}:
                if answer.get("quantity") in (None, "") or not answer.get("capital_destination"):
                    raise ValueError(f"decision response missing action quantity/destination: {pid}")
        if pid == "MAIN_CANDIDATE":
            for field in ("candidate_name", "opportunity_status"):
                if answer.get(field) in (None, ""):
                    raise ValueError(f"decision response missing MAIN_CANDIDATE.{field}")
            candidate_code = str(answer.get("candidate_code") or "").strip()
            candidate_name = str(answer.get("candidate_name") or "").strip()
            if not candidate_code and candidate_name != "现金":
                raise ValueError("decision response missing MAIN_CANDIDATE.candidate_code for security candidate")
        if pid.startswith(("DISCOVERY:", "OBSERVATION:")):
            if not _normalize_opportunity_status(answer.get("opportunity_status")):
                raise ValueError(f"decision response missing registered opportunity_status: {pid}")
            if str(answer.get("disposition") or "").upper() not in {"ADMIT", "REJECT", "RETAIN", "EXIT"}:
                raise ValueError(f"decision response missing valid opportunity disposition: {pid}")
            if not str(answer.get("reason") or "").strip():
                raise ValueError(f"decision response missing opportunity reason: {pid}")
        if pid.startswith("DISCOVERY:") and str(answer.get("disposition") or "").upper() == "ADMIT":
            for field in ("name", "thscode", "thesis", "falsifier", "next_decision_information", "information_value_reason"):
                if not str(answer.get(field) or "").strip():
                    raise ValueError(f"decision response ADMIT requires {pid}.{field}")
        if pid.startswith("OBSERVATION:") and str((by_id.get(pid) or {}).get("role_contract") or "") == "CONTINUOUS_INFORMATION_V1" and str(answer.get("disposition") or "").upper() == "EXIT":
            for field in ("information_value_reason", "continuity_cost_assessment"):
                if not str(answer.get(field) or "").strip():
                    raise ValueError(f"decision response EXIT requires continuous-information rationale {pid}.{field}")

    next_answer = answers.get("NEXT_UNIT_CAPITAL_USE") or {}
    compared_capital_states = next_answer.get("compared_capital_states_as_business_state_names")
    if compared_capital_states in (None, "", []):
        compared_capital_states = next_answer.get("compared_capital_states")
    zero_amount_decisive_reason = str(
        next_answer.get("zero_amount_decisive_reason_if_zero")
        or next_answer.get("zero_amount_decisive_reason")
        or ""
    ).strip()
    for field in (
        "new_amount_yuan", "post_action_deployable_cash", "future_opportunity_capacity",
        "cash_opportunity_cost", "alternative_capital_use_review",
        "concentration_account_structure_effect", "selected_state_reason",
    ):
        if next_answer.get(field) in (None, "", []):
            raise ValueError(f"decision response missing NEXT_UNIT_CAPITAL_USE.{field}")
    if compared_capital_states in (None, "", []):
        raise ValueError("decision response missing NEXT_UNIT_CAPITAL_USE.compared_capital_states_as_business_state_names")
    try:
        new_amount = _parse_yuan_amount(next_answer.get("new_amount_yuan"), "NEXT_UNIT_CAPITAL_USE.new_amount_yuan")
        post_cash = _parse_yuan_amount(next_answer.get("post_action_deployable_cash"), "NEXT_UNIT_CAPITAL_USE.post_action_deployable_cash")
    except ValueError as exc:
        raise ValueError(str(exc))
    if new_amount < 0 or post_cash < 0:
        raise ValueError("NEXT_UNIT_CAPITAL_USE amount/cash must be non-negative")
    if new_amount == 0 and not zero_amount_decisive_reason:
        raise ValueError("decision response requires zero_amount_decisive_reason_if_zero when new amount is zero")

    layer_map = {"A_SHARE_STYLE_FEEDBACK": "layer_2_a_share_internal", "ETF_RELATIVE_STRENGTH": "layer_3_etf_opportunity_capital"}
    consumed = {"request_id": str(source.get("parent_request_id") or source.get("request_id") or "").strip()}
    domains = {"layer_1_external_cross_market": [], "layer_2_a_share_internal": [], "layer_3_etf_opportunity_capital": []}
    consumable_states = {"SATISFIED"}
    requirements = work_package.get("evidence_requirements") or []
    qualified_domains = set()
    for requirement in requirements:
        if str(requirement.get("satisfaction") or "").upper() not in consumable_states:
            continue
        domain = layer_map.get(requirement.get("evidence_class"), "layer_1_external_cross_market")
        qualified_domains.add(domain)
        pid = requirement.get("target_problem_id")
        answer = answers.get(pid) or {}
        impact = answer.get("evidence_decision_impact") or []
        if requirement.get("required") and not impact:
            raise ValueError(f"decision response missing evidence impact: {pid}")
        evidence_id = requirement.get("requirement_id")
        if evidence_id in impact or (impact == ["ALL_REQUIRED"] and requirement.get("required")):
            domains[domain].append(evidence_id)

    missing_qualified = [domain for domain in sorted(qualified_domains) if not domains[domain]]
    if missing_qualified:
        raise ValueError("decision response missing qualified evidence consumption: " + ",".join(missing_qualified))
    consumed.update(domains)

    action_map = {"HOLD": "持有管理", "REDUCE": "降低风险", "EXIT": "退出"}
    position_reviews = []
    lifecycle = {}
    for item in graph:
        pid = str(item.get("problem_id") or "")
        if not pid.startswith("HOLDING:"):
            continue
        answer = answers[pid]
        code = pid.split(":", 1)[1]
        action = _normalize_holding_action(answer.get("final_action"))
        if action not in action_map:
            raise ValueError(f"holding final_action must be HOLD/REDUCE/EXIT: {pid}")
        alternatives = item.get("alternatives") or {}
        states = {key: str(alternatives.get(key) or "").strip() for key in ("HOLD", "REDUCE", "EXIT")}
        if any(not states[key] for key in states):
            raise ValueError(f"decision work package missing holding alternatives: {pid}")
        quantity = answer.get("quantity") if action in {"REDUCE", "EXIT"} else 0
        destination = answer.get("capital_destination") if action in {"REDUCE", "EXIT"} else "继续持有"
        lifecycle[f"{item.get('security') or code}（{code}）"] = action_map[action]
        position_reviews.append({
            "security_code": code,
            "security_name": item.get("security") or code,
            "current_quantity": item.get("current_quantity"),
            "current_action": action_map[action],
            "release_quantity": quantity,
            "capital_destination": destination,
            "holding_state_risk_reward_evidence": answer["capital_comparison"],
            "holding_thesis_status": answer["capital_comparison"],
            "risk_reduction_or_exit_condition": answer["next_change_condition"],
            "higher_efficiency_alternative": answer["higher_efficiency_alternative"],
            "capital_occupancy_reason": answer["capital_occupancy_reason"],
            "continued_holding_opportunity_cost": answer["capital_comparison"],
            "action_changes_now": action in {"REDUCE", "EXIT"},
            "next_change_condition": answer["next_change_condition"],
            "action_detail": answer.get("action_detail") or (f"{action_map[action]} {quantity}" if action in {"REDUCE", "EXIT"} else "继续持有"),
            "capital_use": {
                "continued_holding_vs_cash": answer["capital_comparison"],
                "alternative_capital_uses_review": answer["higher_efficiency_alternative"],
                "position_capital_states": states,
                "quantity": quantity,
                "capital_destination": destination,
            },
        })

    held_add_reviews = []
    for item in graph:
        pid = str(item.get("problem_id") or "")
        if not pid.startswith("HELD_ETF_ADD:"):
            continue
        answer = answers[pid]
        code = pid.split(":", 1)[1]
        add_action = str(answer.get("final_action") or "").upper()
        held_add_reviews.append({
            "security_code": code,
            "eligible_for_additional_capital_review": True,
            "conclusion": answer.get("final_action"),
            "reason": answer.get("capital_comparison"),
        })

    opportunity_reviews = []
    observation_eligibility_reviews = []
    observation_management = []
    for item in graph:
        pid = str(item.get("problem_id") or "")
        if not pid.startswith(("DISCOVERY:", "OBSERVATION:")):
            continue
        answer = answers[pid]
        code = pid.split(":", 1)[1]
        disposition = str(answer.get("disposition") or "").upper()
        opportunity_reviews.append({
            "security_code": code, "code": code, "security_name": item.get("security") or code,
            "category": "OBSERVATION_EVALUATION_INPUT" if pid.startswith("DISCOVERY:") else "OBSERVED_ETF",
            "opportunity_status": _normalize_opportunity_status(answer.get("opportunity_status")),
            "conclusion": answer.get("final_action"), "reason": answer.get("reason"),
        })
        if pid.startswith("DISCOVERY:"):
            if disposition not in {"ADMIT", "REJECT"}:
                raise ValueError(f"Discovery disposition must be ADMIT/REJECT: {pid}")
            observation_eligibility_reviews.append({"code": code, "disposition": disposition, "reason": answer.get("reason")})
            if disposition == "ADMIT":
                observation_management.append({
                    "code": code, "action": "ADMIT",
                    "name": answer["name"], "thscode": answer["thscode"],
                    "thesis": answer["thesis"], "falsifier": answer["falsifier"],
                    "next_decision_information": answer["next_decision_information"],
                    "information_value_reason": answer["information_value_reason"],
                })
        else:
            if disposition not in {"RETAIN", "EXIT"}:
                raise ValueError(f"Observation disposition must be RETAIN/EXIT: {pid}")
            if disposition == "EXIT":
                exit_item = {"code": code, "action": "EXIT", "reason": answer.get("reason")}
                if str(item.get("role_contract") or "") == "CONTINUOUS_INFORMATION_V1":
                    exit_item["information_value_reason"] = answer.get("information_value_reason")
                    exit_item["continuity_cost_assessment"] = answer.get("continuity_cost_assessment")
                observation_management.append(exit_item)
            else:
                thesis = item.get("existing_thesis_state") or {}
                required_thesis = ("name", "thscode", "thesis", "falsifier", "next_decision_information", "information_value_reason")
                missing_thesis = [key for key in required_thesis if not str(thesis.get(key) or "").strip()]
                if missing_thesis:
                    raise ValueError(f"decision work package missing canonical Observation thesis state for {code}: {','.join(missing_thesis)}")
                observation_management.append({
                    "code": code,
                    "action": "RETAIN",
                    **{key: thesis[key] for key in required_thesis},
                })

    capital_opportunity_reviews = _capital_competition_opportunity_reviews(opportunity_reviews, graph)
    role_contract_v1 = str(work_package.get("business_role_reconciliation_contract") or "").upper() == "V1"
    business_role_reconciliation = None
    if role_contract_v1:
        holding_etfs = []
        for review in position_reviews:
            code = str(review.get("security_code") or "")
            if any(str(x.get("problem_id") or "") == f"HELD_ETF_ADD:{code}" for x in graph):
                add_review = next((x for x in held_add_reviews if str(x.get("security_code") or "") == code), {})
                holding_etfs.append({
                    "code": code,
                    "name": review.get("security_name"),
                    "capital_occupancy": review.get("current_action"),
                    "additional_capital_review": add_review.get("conclusion"),
                })

        observation_etfs = []
        opportunity_etfs = []
        opportunity_by_code = {}
        discovery_summary = {"evaluated": 0, "entered_opportunity_role": 0, "rejected_from_opportunity_role": 0}
        for review in opportunity_reviews:
            status = str(review.get("opportunity_status") or "")
            is_opportunity = status != "无机会"
            code = str(review.get("code") or review.get("security_code") or "")
            if review.get("category") == "OBSERVED_ETF":
                management = next((x for x in observation_management if str(x.get("code") or "") == code), {})
                observation_etfs.append({
                    "code": code,
                    "name": review.get("security_name"),
                    "observation_action": management.get("action"),
                    "information_value_reason": management.get("information_value_reason") or review.get("reason"),
                    "current_opportunity_status": status,
                })
            else:
                discovery_summary["evaluated"] += 1
            if is_opportunity:
                source_role = "观察ETF" if review.get("category") == "OBSERVED_ETF" else "全市场机会发现"
                existing_opportunity = opportunity_by_code.get(code)
                if existing_opportunity is None:
                    existing_opportunity = {
                        "code": code,
                        "name": review.get("security_name"),
                        "source": source_role,
                        "opportunity_status": status,
                        "execution_evidence": review.get("reason"),
                    }
                    opportunity_by_code[code] = existing_opportunity
                    opportunity_etfs.append(existing_opportunity)
                else:
                    if source_role not in existing_opportunity["source"].split(" + "):
                        existing_opportunity["source"] += " + " + source_role
                    evidence = str(review.get("reason") or "").strip()
                    if evidence and evidence not in str(existing_opportunity.get("execution_evidence") or ""):
                        existing_opportunity["execution_evidence"] = "；".join(
                            value for value in (str(existing_opportunity.get("execution_evidence") or "").strip(), evidence) if value
                        )
                if review.get("category") != "OBSERVED_ETF":
                    discovery_summary["entered_opportunity_role"] += 1
            elif review.get("category") != "OBSERVED_ETF":
                discovery_summary["rejected_from_opportunity_role"] += 1

        business_role_reconciliation = {
            "持仓ETF": holding_etfs,
            "观察ETF": observation_etfs,
            "机会ETF": opportunity_etfs,
            "全市场机会发现": discovery_summary,
        }

    main_answer = answers["MAIN_CANDIDATE"]
    risk_answer = answers["RISK_PERMISSION"]
    risk_permission = _normalize_risk_permission(risk_answer["final_action"])
    if not risk_permission:
        raise ValueError("decision response RISK_PERMISSION.final_action has no registered formal permission")
    raw_main_opportunity_status = str(main_answer["opportunity_status"]).strip()
    main_opportunity_status = _normalize_opportunity_status(raw_main_opportunity_status)
    if raw_main_opportunity_status == "无新增交易机会" and (
        str(main_answer.get("candidate_name") or "").strip() != "现金" or new_amount != 0
    ):
        raise ValueError("无新增交易机会 is only valid for cash-selected zero-new-capital decisions")
    state_problem_map = {
        "现金": "DEPLOYABLE_CASH",
        "全部实际持仓继续占资": "RELEASABLE_CAPITAL",
        "全部持仓ETF追加": "CONCENTRATION_COMMON_RISK",
        "全部正式观察ETF": "CONCENTRATION_COMMON_RISK",
        "12只Discovery临时评估对象": "CONCENTRATION_COMMON_RISK",
        "561980条件释放资本": "RELEASABLE_CAPITAL",
        "159981下一节点Confirm": "TRIAL_CONFIRM_CAPACITY",
    }
    if role_contract_v1:
        forbidden_aggregate_states = [
            str(x or "").strip() for x in compared_capital_states
            if not isinstance(x, dict) and ("观察ETF" in str(x or "") or "Discovery" in str(x or "") or "全市场机会发现" in str(x or ""))
        ]
        if forbidden_aggregate_states:
            raise ValueError(
                "business-role V1 capital competition cannot use observation/discovery aggregates as capital states: "
                + ",".join(forbidden_aggregate_states)
            )
    canonical_capital_states = []
    for raw_state in compared_capital_states:
        if isinstance(raw_state, dict):
            canonical_capital_states.append(raw_state)
            continue
        state_name = str(raw_state or "").strip()
        if not state_name:
            raise ValueError("NEXT_UNIT_CAPITAL_USE compared_capital_states contains empty state")
        problem_id = state_problem_map.get(state_name)
        if not problem_id:
            if "现金" in state_name:
                problem_id = "DEPLOYABLE_CASH"
            elif "释放" in state_name:
                problem_id = "RELEASABLE_CAPITAL"
            elif "Confirm" in state_name or "Trial" in state_name:
                problem_id = "TRIAL_CONFIRM_CAPACITY"
            elif "追加" in state_name or "观察" in state_name or "Discovery" in state_name:
                problem_id = "CONCENTRATION_COMMON_RISK"
            else:
                raise ValueError(f"NEXT_UNIT_CAPITAL_USE capital state has no business-semantics owner: {state_name}")
        owner = answers.get(problem_id) or {}
        action = str(owner.get("final_action") or "").strip()
        comparison = str(owner.get("capital_comparison") or "").strip()
        if not action or not comparison:
            raise ValueError(f"decision response missing capital-state business semantics: {state_name}->{problem_id}")
        selected = state_name in str(next_answer.get("final_action") or "")
        canonical_capital_states.append({
            "state_name": state_name,
            "capital_action": action,
            "remaining_deployable_cash": post_cash,
            "why_not_selected": "已选中" if selected else comparison,
            "opportunity_cost_if_selected": str(next_answer.get("cash_opportunity_cost") or comparison),
        })

    _validate_material_state_transitions(
        answers, work_package,
        risk_permission=risk_permission,
        candidate_code=str(main_answer.get("candidate_code") or ""),
        opportunity_status=main_opportunity_status,
        position_reviews=position_reviews,
    )

    projected = json.loads(json.dumps(source))
    projected.update({
        "risk_permission": risk_permission,
        "candidate_code": str(main_answer.get("candidate_code") or ""),
        "candidate_name": str(main_answer["candidate_name"]),
        "main_candidate": (
            f"{main_answer['candidate_name']}（{main_answer['candidate_code']}）"
            if str(main_answer.get("candidate_code") or "").strip()
            else str(main_answer["candidate_name"])
        ),
        "opportunity_status": main_opportunity_status,
        "lifecycle": lifecycle,
        "managed_position_reviews": position_reviews,
        "etf_opportunity_reviews": opportunity_reviews,
        "observation_eligibility_reviews": observation_eligibility_reviews,
        "observation_management": observation_management,
        "business_role_reconciliation": business_role_reconciliation,
        "continued_holding_opportunity_cost": "；".join(str(x.get("continued_holding_opportunity_cost") or "") for x in position_reviews),
        "action_changes_now": any(bool(x.get("action_changes_now")) for x in position_reviews) or new_amount > 0,
        "next_change_condition": next_answer["next_change_condition"],
        "next_unit_capital_use": next_answer["final_action"],
        "capital_competition": {
            "next_unit_capital_use": next_answer["final_action"],
            "full_competition_completed": True,
            "releasable_capital_reviewed": True,
            "post_action_deployable_cash": post_cash,
            "future_opportunity_capacity": next_answer["future_opportunity_capacity"],
            "cash_opportunity_cost": next_answer["cash_opportunity_cost"],
            "alternative_capital_use_review": next_answer["alternative_capital_use_review"],
            "concentration_account_structure_effect": next_answer["concentration_account_structure_effect"],
            "selected_state_reason": next_answer["selected_state_reason"],
            "new_amount_yuan": new_amount,
            "zero_amount_decisive_reason": zero_amount_decisive_reason,
            "compared_capital_states": canonical_capital_states,
            "held_etf_add_capital_reviews": held_add_reviews,
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
    "decision_evidence_consumption",
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

# Ephemeral, opt-in forensic tracing for controlled historical Formal replay.
# This state exists only in process memory and never enters any business payload.
_FORENSIC_TRACE = {"enabled": False, "baseline_graph_sha256": "", "first_divergence_reported": False, "previous": None}

def reset_formal_replay_forensic_trace(enabled: bool = False) -> None:
    """Reset this process-local diagnostic trace; does not touch domain state."""
    _FORENSIC_TRACE.update({"enabled": bool(enabled), "baseline_graph_sha256": "", "first_divergence_reported": False, "previous": None})

def emit_formal_replay_forensic_fingerprint(
    stage: str,
    *,
    obj: Any = None,
    dwp: Any = None,
    object_type: str = "",
    source_type: str = "",
    provenance: str = "",
    source_fingerprint_value: str = "",
    historical: bool = False,
) -> None:
    """Write a redacted runtime fingerprint to stdout only when explicitly enabled."""
    if not _FORENSIC_TRACE["enabled"] or not historical:
        return
    dwp_obj = dwp if isinstance(dwp, dict) else None
    graph_present = bool(dwp_obj is not None and "problem_graph" in dwp_obj)
    graph = dwp_obj.get("problem_graph") if graph_present else None
    graph = graph if isinstance(graph, list) else []
    problem_ids = [str(item.get("problem_id") or "") for item in graph if isinstance(item, dict) and item.get("problem_id")]
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sha = lambda value: hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
    graph_sha = sha(graph) if graph_present else ""
    ids_sha = sha(problem_ids)
    dwp_sha = sha(dwp_obj) if dwp_obj is not None else ""
    previous = _FORENSIC_TRACE.get("previous")
    record = {
        "event": "FORMAL_REPLAY_FORENSIC_FINGERPRINT",
        "stage": stage,
        "problem_ids": problem_ids,
        "problem_ids_sha256": ids_sha,
        "problem_graph_sha256": graph_sha,
        "dwp_sha256": dwp_sha,
        "object_type": object_type or (type(obj).__name__ if obj is not None else "null"),
        "source_type": source_type or (str((obj or {}).get("source_type") or (obj or {}).get("request_type") or "") if isinstance(obj, dict) else ""),
        "provenance": provenance,
        "object_identity": {"object_id": hex(id(obj)) if obj is not None else "", "dwp_id": hex(id(dwp)) if dwp is not None else "", "graph_id": hex(id(graph)) if graph_present else ""},
        "mutation_evidence": {
            "previous_stage": previous.get("stage") if previous else "",
            "same_graph_content_as_previous": bool(previous and graph_sha and graph_sha == previous.get("problem_graph_sha256")),
            "same_dwp_object_as_previous": bool(previous and dwp is not None and hex(id(dwp)) == previous.get("dwp_id")),
            "graph_present_in_object": graph_present,
        },
        "source_fingerprint": source_fingerprint_value,
    }
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
    if stage == "A_HISTORICAL_PACKET" and graph_sha:
        _FORENSIC_TRACE["baseline_graph_sha256"] = graph_sha
    baseline = _FORENSIC_TRACE.get("baseline_graph_sha256")
    if stage != "A_HISTORICAL_PACKET" and graph_sha and baseline and graph_sha != baseline and not _FORENSIC_TRACE["first_divergence_reported"]:
        print(json.dumps({"event": "FIRST_DIVERGENCE_STAGE", "stage": stage, "historical_graph_sha256": baseline, "observed_graph_sha256": graph_sha}, sort_keys=True), flush=True)
        _FORENSIC_TRACE["first_divergence_reported"] = True
    _FORENSIC_TRACE["previous"] = {"stage": stage, "problem_graph_sha256": graph_sha, "dwp_id": hex(id(dwp)) if dwp is not None else ""}


_FORMAL_OPPORTUNITY_ALIASES = {
    "无机会": "无机会",
    "无新增交易机会": "无机会",
    "观察机会": "观察机会",
    "Trial机会": "Trial机会",
    "Confirm机会": "Confirm机会",
}

def _normalize_opportunity_status(value: Any) -> str:
    return _FORMAL_OPPORTUNITY_ALIASES.get(str(value or "").strip(), "")

_FORMAL_RISK_PERMISSION_VALUES = ("禁止新增", "允许Trial", "允许Confirm")

def _normalize_risk_permission(value: Any) -> str:
    """Project an actor risk-permission sentence onto the existing formal enum.

    The actor-facing DWP asks for a business judgment, so final_action may carry
    an explanatory suffix. Persistence owns only the leading registered
    permission. Ambiguous or unregistered text remains fail-closed.
    """
    text = str(value or "").strip()
    if text in _FORMAL_RISK_PERMISSION_VALUES:
        return text
    matches = [item for item in _FORMAL_RISK_PERMISSION_VALUES if text.startswith(item)]
    if len(matches) == 1:
        suffix = text[len(matches[0]):]
        if not suffix or suffix[0] in "；;，,。:： （(":
            return matches[0]
    return ""

_HOLDING_ACTION_ALIASES = {
    "HOLD": "HOLD",
    "持有": "HOLD",
    "持有管理": "HOLD",
    "持仓管理": "HOLD",
    "REDUCE": "REDUCE",
    "降低风险": "REDUCE",
    "EXIT": "EXIT",
    "退出": "EXIT",
    "全部退出": "EXIT",
}


def _normalize_holding_action(value: Any) -> str:
    raw = str(value or "").strip()
    return _HOLDING_ACTION_ALIASES.get(raw.upper(), _HOLDING_ACTION_ALIASES.get(raw, raw.upper()))


def _parse_yuan_amount(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    raw = value
    if isinstance(value, str):
        raw = value.strip().replace(",", "").replace("人民币", "")
        if raw.endswith("元"):
            raw = raw[:-1].strip()
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be numeric")
    return number


def source_fingerprint(source: dict[str, Any]) -> str:
    body = {k: v for k, v in source.items() if k not in {"fingerprint", "status", "projection_status"}}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

def validate_decision_evidence_consumption(value: Any, *, parent_request_id: str = "") -> str:
    """Validate explicit evidence consumption without making an investment judgment."""
    if not isinstance(value, dict):
        return "decision_evidence_consumption must be an object"
    required = (
        "request_id",
        "layer_1_external_cross_market",
        "layer_2_a_share_internal",
        "layer_3_etf_opportunity_capital",
        "discovery_to_capital_competition_consumed",
        "all_managed_positions_sell_chain_consumed",
        "held_etf_additional_capital_consumed",
        "next_unit_capital_use_consumed",
    )
    layer_keys = {
        "layer_1_external_cross_market",
        "layer_2_a_share_internal",
        "layer_3_etf_opportunity_capital",
    }
    missing = [
        key for key in required
        if key not in value or (key not in layer_keys and value[key] in (None, "", [], {}))
    ]
    if missing:
        return "decision_evidence_consumption missing: " + ",".join(missing)
    for key in layer_keys:
        if not isinstance(value.get(key), list):
            return f"decision_evidence_consumption.{key} must be a list"
    if parent_request_id and str(value.get("request_id") or "").strip() != parent_request_id:
        return "decision_evidence_consumption request_id must match parent request"
    for key in (
        "discovery_to_capital_competition_consumed",
        "all_managed_positions_sell_chain_consumed",
        "held_etf_additional_capital_consumed",
        "next_unit_capital_use_consumed",
    ):
        if value.get(key) is not True:
            return f"decision_evidence_consumption.{key} must be true"
    return ""


def _transition_attribution_error(answer: dict, *, problem_id: str, qualified_requirement_ids: set[str]) -> str:
    attribution = answer.get("state_transition_attribution")
    if not isinstance(attribution, dict):
        return f"decision response material state transition requires {problem_id}.state_transition_attribution"
    delta = str(attribution.get("material_evidence_delta") or "").strip()
    evidence_ids = attribution.get("evidence_requirement_ids")
    if not delta:
        return f"decision response material state transition requires {problem_id}.material_evidence_delta"
    if not isinstance(evidence_ids, list) or not evidence_ids:
        return f"decision response material state transition requires {problem_id}.evidence_requirement_ids"
    invalid = [str(x) for x in evidence_ids if str(x) not in qualified_requirement_ids]
    if invalid:
        return f"decision response material state transition cites unqualified evidence for {problem_id}: " + ",".join(invalid)
    return ""


def _validate_material_state_transitions(
    answers: dict[str, Any], work_package: dict[str, Any], *, risk_permission: str,
    candidate_code: str, opportunity_status: str, position_reviews: list[dict[str, Any]],
) -> None:
    baseline = work_package.get("previous_formal_decision_baseline") or {}
    if not isinstance(baseline, dict) or not baseline:
        return
    requirements = work_package.get("evidence_requirements") or []
    qualified_by_problem: dict[str, set[str]] = {}
    for item in requirements:
        if not isinstance(item, dict) or str(item.get("satisfaction") or "").upper() not in {"SATISFIED", "DEGRADED"}:
            continue
        pid = str(item.get("target_problem_id") or "")
        rid = str(item.get("requirement_id") or "")
        if pid and rid:
            qualified_by_problem.setdefault(pid, set()).add(rid)

    prior_risk = _normalize_risk_permission(baseline.get("risk_permission"))
    if prior_risk and risk_permission != prior_risk:
        error = _transition_attribution_error(answers.get("RISK_PERMISSION") or {}, problem_id="RISK_PERMISSION", qualified_requirement_ids=qualified_by_problem.get("RISK_PERMISSION", set()))
        if error:
            raise ValueError(error)

    prior_code = str(baseline.get("candidate_code") or "").strip()
    prior_status = _normalize_opportunity_status(baseline.get("opportunity_status"))
    if (prior_code and candidate_code != prior_code) or (prior_status and opportunity_status != prior_status):
        error = _transition_attribution_error(answers.get("MAIN_CANDIDATE") or {}, problem_id="MAIN_CANDIDATE", qualified_requirement_ids=qualified_by_problem.get("MAIN_CANDIDATE", set()))
        if error:
            raise ValueError(error)

    prior_holding = baseline.get("holding_actions") or {}
    if isinstance(prior_holding, dict):
        for review in position_reviews:
            code = str(review.get("security_code") or "")
            prior_action = _normalize_holding_action(prior_holding.get(code))
            current_action = _normalize_holding_action(review.get("current_action"))
            if prior_action and current_action and prior_action != current_action:
                pid = f"HOLDING:{code}"
                error = _transition_attribution_error(answers.get(pid) or {}, problem_id=pid, qualified_requirement_ids=qualified_by_problem.get(pid, set()))
                if error:
                    raise ValueError(error)


def _capital_competition_opportunity_reviews(reviews: list[dict[str, Any]], graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse distinct role reviews for one ETF into one capital state.

    An ETF can already be a persistent Observation and also surface in the
    node-local Discovery set. Keep both role-level reviews for audit, but count
    the instrument once in executable capital competition. Conflicting status
    judgments fail closed and must be reconciled by the decision actor.
    """
    roles_by_code: dict[str, set[str]] = {}
    for item in graph:
        problem_id = str(item.get("problem_id") or "")
        if problem_id.startswith("OBSERVATION:"):
            code = problem_id.split(":", 1)[1].strip()
            category = "OBSERVED_ETF"
        elif problem_id.startswith("DISCOVERY:"):
            code = problem_id.split(":", 1)[1].strip()
            category = "OBSERVATION_EVALUATION_INPUT"
        else:
            continue
        if code:
            roles_by_code.setdefault(code, set()).add(category)

    allowed_overlap = {"OBSERVED_ETF", "OBSERVATION_EVALUATION_INPUT"}
    result: list[dict[str, Any]] = []
    index_by_code: dict[str, int] = {}
    seen_roles: dict[str, set[str]] = {}
    for review in reviews:
        code = str(review.get("code") or review.get("security_code") or "").strip()
        category = str(review.get("category") or "").strip().upper()
        expected_roles = roles_by_code.get(code, set())
        if not code or category not in expected_roles:
            raise ValueError("capital opportunity review does not match its decision-work-package role")
        if category in seen_roles.setdefault(code, set()):
            raise ValueError("duplicate capital opportunity review role for " + code)
        seen_roles[code].add(category)

        if code not in index_by_code:
            item = dict(review)
            item["category"] = "OBSERVED_ETF" if allowed_overlap <= expected_roles else category
            index_by_code[code] = len(result)
            result.append(item)
            continue

        if expected_roles != allowed_overlap:
            raise ValueError("duplicate capital opportunity identity for " + code)
        existing = result[index_by_code[code]]
        if str(existing.get("opportunity_status") or "") != str(review.get("opportunity_status") or ""):
            raise ValueError("conflicting opportunity status across roles for " + code)
        existing["category"] = "OBSERVED_ETF"
        existing["conclusion"] = "；".join(
            value for value in (str(existing.get("conclusion") or "").strip(), str(review.get("conclusion") or "").strip()) if value
        )
        existing["reason"] = "；".join(
            value for value in (str(existing.get("reason") or "").strip(), str(review.get("reason") or "").strip()) if value
        )
    return result


def project_decision_response(source: dict[str, Any], response: dict[str, Any], work_package: dict[str, Any], *, forensic_historical_replay: bool = False) -> dict[str, Any]:
    """Project actor-only business answers into the canonical Business Decision Source."""
    if forensic_historical_replay:
        emit_formal_replay_forensic_fingerprint("E_PROJECT_ENTRY", obj=source, dwp=work_package, object_type="project_decision_response.source", provenance="historical_replay", historical=True)
        emit_formal_replay_forensic_fingerprint("F_SOURCE_INTERNAL_DWP", obj=source, dwp=source.get("decision_work_package"), object_type="project_decision_response.source", provenance="historical_replay", historical=True)
    if not isinstance(response, dict) or not isinstance(work_package, dict):
        raise ValueError("decision response/work package must be objects")
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("decision response requires answers keyed by problem_id")
    graph = [x for x in (work_package.get("problem_graph") or []) if isinstance(x, dict)]
    if forensic_historical_replay:
        emit_formal_replay_forensic_fingerprint("G_COMPLETENESS_VALIDATOR_GRAPH", obj=work_package, dwp={"problem_graph": graph}, object_type="completeness_validator.problem_graph", provenance="historical_replay", historical=True)
    required_ids = [str(x.get("problem_id") or "") for x in graph if x.get("problem_id")]
    missing = [pid for pid in required_ids if pid not in answers]
    if missing:
        raise ValueError("decision response missing problem_ids: " + ",".join(missing))

    by_id = {str(x.get("problem_id") or ""): x for x in graph}
    for pid in required_ids:
        answer = answers[pid]
        if not isinstance(answer, dict):
            raise ValueError(f"decision response answer must be an object: {pid}")
        for field in ("final_action", "capital_comparison", "next_change_condition", "evidence_decision_impact"):
            if answer.get(field) in (None, "", [], {}):
                raise ValueError(f"decision response missing {pid}.{field}")
        if pid.startswith("HOLDING:"):
            if not answer.get("capital_occupancy_reason") or not answer.get("higher_efficiency_alternative"):
                raise ValueError(f"decision response missing holding capital rationale: {pid}")
            if _normalize_holding_action(answer.get("final_action")) in {"REDUCE", "EXIT"}:
                if answer.get("quantity") in (None, "") or not answer.get("capital_destination"):
                    raise ValueError(f"decision response missing action quantity/destination: {pid}")
        if pid == "MAIN_CANDIDATE":
            for field in ("candidate_name", "opportunity_status"):
                if answer.get(field) in (None, ""):
                    raise ValueError(f"decision response missing MAIN_CANDIDATE.{field}")
            candidate_code = str(answer.get("candidate_code") or "").strip()
            candidate_name = str(answer.get("candidate_name") or "").strip()
            if not candidate_code and candidate_name != "现金":
                raise ValueError("decision response missing MAIN_CANDIDATE.candidate_code for security candidate")
        if pid.startswith(("DISCOVERY:", "OBSERVATION:")):
            if not _normalize_opportunity_status(answer.get("opportunity_status")):
                raise ValueError(f"decision response missing registered opportunity_status: {pid}")
            if str(answer.get("disposition") or "").upper() not in {"ADMIT", "REJECT", "RETAIN", "EXIT"}:
                raise ValueError(f"decision response missing valid opportunity disposition: {pid}")
            if not str(answer.get("reason") or "").strip():
                raise ValueError(f"decision response missing opportunity reason: {pid}")
        if pid.startswith("DISCOVERY:") and str(answer.get("disposition") or "").upper() == "ADMIT":
            for field in ("name", "thscode", "thesis", "falsifier", "next_decision_information", "information_value_reason"):
                if not str(answer.get(field) or "").strip():
                    raise ValueError(f"decision response ADMIT requires {pid}.{field}")
        if pid.startswith("OBSERVATION:") and str((by_id.get(pid) or {}).get("role_contract") or "") == "CONTINUOUS_INFORMATION_V1" and str(answer.get("disposition") or "").upper() == "EXIT":
            for field in ("information_value_reason", "continuity_cost_assessment"):
                if not str(answer.get(field) or "").strip():
                    raise ValueError(f"decision response EXIT requires continuous-information rationale {pid}.{field}")

    next_answer = answers.get("NEXT_UNIT_CAPITAL_USE") or {}
    compared_capital_states = next_answer.get("compared_capital_states_as_business_state_names")
    if compared_capital_states in (None, "", []):
        compared_capital_states = next_answer.get("compared_capital_states")
    zero_amount_decisive_reason = str(
        next_answer.get("zero_amount_decisive_reason_if_zero")
        or next_answer.get("zero_amount_decisive_reason")
        or ""
    ).strip()
    for field in (
        "new_amount_yuan", "post_action_deployable_cash", "future_opportunity_capacity",
        "cash_opportunity_cost", "alternative_capital_use_review",
        "concentration_account_structure_effect", "selected_state_reason",
    ):
        if next_answer.get(field) in (None, "", []):
            raise ValueError(f"decision response missing NEXT_UNIT_CAPITAL_USE.{field}")
    if compared_capital_states in (None, "", []):
        raise ValueError("decision response missing NEXT_UNIT_CAPITAL_USE.compared_capital_states_as_business_state_names")
    try:
        new_amount = _parse_yuan_amount(next_answer.get("new_amount_yuan"), "NEXT_UNIT_CAPITAL_USE.new_amount_yuan")
        post_cash = _parse_yuan_amount(next_answer.get("post_action_deployable_cash"), "NEXT_UNIT_CAPITAL_USE.post_action_deployable_cash")
    except ValueError as exc:
        raise ValueError(str(exc))
    if new_amount < 0 or post_cash < 0:
        raise ValueError("NEXT_UNIT_CAPITAL_USE amount/cash must be non-negative")
    if new_amount == 0 and not zero_amount_decisive_reason:
        raise ValueError("decision response requires zero_amount_decisive_reason_if_zero when new amount is zero")

    layer_map = {"A_SHARE_STYLE_FEEDBACK": "layer_2_a_share_internal", "ETF_RELATIVE_STRENGTH": "layer_3_etf_opportunity_capital"}
    consumed = {"request_id": str(source.get("parent_request_id") or source.get("request_id") or "").strip()}
    domains = {"layer_1_external_cross_market": [], "layer_2_a_share_internal": [], "layer_3_etf_opportunity_capital": []}
    consumable_states = {"SATISFIED"}
    requirements = work_package.get("evidence_requirements") or []
    qualified_domains = set()
    for requirement in requirements:
        if str(requirement.get("satisfaction") or "").upper() not in consumable_states:
            continue
        domain = layer_map.get(requirement.get("evidence_class"), "layer_1_external_cross_market")
        qualified_domains.add(domain)
        pid = requirement.get("target_problem_id")
        answer = answers.get(pid) or {}
        impact = answer.get("evidence_decision_impact") or []
        if requirement.get("required") and not impact:
            raise ValueError(f"decision response missing evidence impact: {pid}")
        evidence_id = requirement.get("requirement_id")
        if evidence_id in impact or (impact == ["ALL_REQUIRED"] and requirement.get("required")):
            domains[domain].append(evidence_id)

    missing_qualified = [domain for domain in sorted(qualified_domains) if not domains[domain]]
    if missing_qualified:
        raise ValueError("decision response missing qualified evidence consumption: " + ",".join(missing_qualified))
    consumed.update(domains)

    action_map = {"HOLD": "持有管理", "REDUCE": "降低风险", "EXIT": "退出"}
    position_reviews = []
    lifecycle = {}
    for item in graph:
        pid = str(item.get("problem_id") or "")
        if not pid.startswith("HOLDING:"):
            continue
        answer = answers[pid]
        code = pid.split(":", 1)[1]
        action = _normalize_holding_action(answer.get("final_action"))
        if action not in action_map:
            raise ValueError(f"holding final_action must be HOLD/REDUCE/EXIT: {pid}")
        alternatives = item.get("alternatives") or {}
        states = {key: str(alternatives.get(key) or "").strip() for key in ("HOLD", "REDUCE", "EXIT")}
        if any(not states[key] for key in states):
            raise ValueError(f"decision work package missing holding alternatives: {pid}")
        quantity = answer.get("quantity") if action in {"REDUCE", "EXIT"} else 0
        destination = answer.get("capital_destination") if action in {"REDUCE", "EXIT"} else "继续持有"
        lifecycle[f"{item.get('security') or code}（{code}）"] = action_map[action]
        position_reviews.append({
            "security_code": code,
            "security_name": item.get("security") or code,
            "current_quantity": item.get("current_quantity"),
            "current_action": action_map[action],
            "release_quantity": quantity,
            "capital_destination": destination,
            "holding_state_risk_reward_evidence": answer["capital_comparison"],
            "holding_thesis_status": answer["capital_comparison"],
            "risk_reduction_or_exit_condition": answer["next_change_condition"],
            "higher_efficiency_alternative": answer["higher_efficiency_alternative"],
            "capital_occupancy_reason": answer["capital_occupancy_reason"],
            "continued_holding_opportunity_cost": answer["capital_comparison"],
            "action_changes_now": action in {"REDUCE", "EXIT"},
            "next_change_condition": answer["next_change_condition"],
            "action_detail": answer.get("action_detail") or (f"{action_map[action]} {quantity}" if action in {"REDUCE", "EXIT"} else "继续持有"),
            "capital_use": {
                "continued_holding_vs_cash": answer["capital_comparison"],
                "alternative_capital_uses_review": answer["higher_efficiency_alternative"],
                "position_capital_states": states,
                "quantity": quantity,
                "capital_destination": destination,
            },
        })

    held_add_reviews = []
    for item in graph:
        pid = str(item.get("problem_id") or "")
        if not pid.startswith("HELD_ETF_ADD:"):
            continue
        answer = answers[pid]
        code = pid.split(":", 1)[1]
        add_action = str(answer.get("final_action") or "").upper()
        held_add_reviews.append({
            "security_code": code,
            "eligible_for_additional_capital_review": True,
            "conclusion": answer.get("final_action"),
            "reason": answer.get("capital_comparison"),
        })

    opportunity_reviews = []
    observation_eligibility_reviews = []
    observation_management = []
    for item in graph:
        pid = str(item.get("problem_id") or "")
        if not pid.startswith(("DISCOVERY:", "OBSERVATION:")):
            continue
        answer = answers[pid]
        code = pid.split(":", 1)[1]
        disposition = str(answer.get("disposition") or "").upper()
        opportunity_reviews.append({
            "security_code": code, "code": code, "security_name": item.get("security") or code,
            "category": "OBSERVATION_EVALUATION_INPUT" if pid.startswith("DISCOVERY:") else "OBSERVED_ETF",
            "opportunity_status": _normalize_opportunity_status(answer.get("opportunity_status")),
            "conclusion": answer.get("final_action"), "reason": answer.get("reason"),
        })
        if pid.startswith("DISCOVERY:"):
            if disposition not in {"ADMIT", "REJECT"}:
                raise ValueError(f"Discovery disposition must be ADMIT/REJECT: {pid}")
            observation_eligibility_reviews.append({"code": code, "disposition": disposition, "reason": answer.get("reason")})
            if disposition == "ADMIT":
                observation_management.append({
                    "code": code, "action": "ADMIT",
                    "name": answer["name"], "thscode": answer["thscode"],
                    "thesis": answer["thesis"], "falsifier": answer["falsifier"],
                    "next_decision_information": answer["next_decision_information"],
                    "information_value_reason": answer["information_value_reason"],
                })
        else:
            if disposition not in {"RETAIN", "EXIT"}:
                raise ValueError(f"Observation disposition must be RETAIN/EXIT: {pid}")
            if disposition == "EXIT":
                exit_item = {"code": code, "action": "EXIT", "reason": answer.get("reason")}
                if str(item.get("role_contract") or "") == "CONTINUOUS_INFORMATION_V1":
                    exit_item["information_value_reason"] = answer.get("information_value_reason")
                    exit_item["continuity_cost_assessment"] = answer.get("continuity_cost_assessment")
                observation_management.append(exit_item)
            else:
                thesis = item.get("existing_thesis_state") or {}
                required_thesis = ("name", "thscode", "thesis", "falsifier", "next_decision_information", "information_value_reason")
                missing_thesis = [key for key in required_thesis if not str(thesis.get(key) or "").strip()]
                if missing_thesis:
                    raise ValueError(f"decision work package missing canonical Observation thesis state for {code}: {','.join(missing_thesis)}")
                observation_management.append({
                    "code": code,
                    "action": "RETAIN",
                    **{key: thesis[key] for key in required_thesis},
                })

    capital_opportunity_reviews = _capital_competition_opportunity_reviews(opportunity_reviews, graph)
    role_contract_v1 = str(work_package.get("business_role_reconciliation_contract") or "").upper() == "V1"
    business_role_reconciliation = None
    if role_contract_v1:
        holding_etfs = []
        for review in position_reviews:
            code = str(review.get("security_code") or "")
            if any(str(x.get("problem_id") or "") == f"HELD_ETF_ADD:{code}" for x in graph):
                add_review = next((x for x in held_add_reviews if str(x.get("security_code") or "") == code), {})
                holding_etfs.append({
                    "code": code,
                    "name": review.get("security_name"),
                    "capital_occupancy": review.get("current_action"),
                    "additional_capital_review": add_review.get("conclusion"),
                })

        observation_etfs = []
        opportunity_etfs = []
        opportunity_by_code = {}
        discovery_summary = {"evaluated": 0, "entered_opportunity_role": 0, "rejected_from_opportunity_role": 0}
        for review in opportunity_reviews:
            status = str(review.get("opportunity_status") or "")
            is_opportunity = status != "无机会"
            code = str(review.get("code") or review.get("security_code") or "")
            if review.get("category") == "OBSERVED_ETF":
                management = next((x for x in observation_management if str(x.get("code") or "") == code), {})
                observation_etfs.append({
                    "code": code,
                    "name": review.get("security_name"),
                    "observation_action": management.get("action"),
                    "information_value_reason": management.get("information_value_reason") or review.get("reason"),
                    "current_opportunity_status": status,
                })
            else:
                discovery_summary["evaluated"] += 1
            if is_opportunity:
                source_role = "观察ETF" if review.get("category") == "OBSERVED_ETF" else "全市场机会发现"
                existing_opportunity = opportunity_by_code.get(code)
                if existing_opportunity is None:
                    existing_opportunity = {
                        "code": code,
                        "name": review.get("security_name"),
                        "source": source_role,
                        "opportunity_status": status,
                        "execution_evidence": review.get("reason"),
                    }
                    opportunity_by_code[code] = existing_opportunity
                    opportunity_etfs.append(existing_opportunity)
                else:
                    if source_role not in existing_opportunity["source"].split(" + "):
                        existing_opportunity["source"] += " + " + source_role
                    evidence = str(review.get("reason") or "").strip()
                    if evidence and evidence not in str(existing_opportunity.get("execution_evidence") or ""):
                        existing_opportunity["execution_evidence"] = "；".join(
                            value for value in (str(existing_opportunity.get("execution_evidence") or "").strip(), evidence) if value
                        )
                if review.get("category") != "OBSERVED_ETF":
                    discovery_summary["entered_opportunity_role"] += 1
            elif review.get("category") != "OBSERVED_ETF":
                discovery_summary["rejected_from_opportunity_role"] += 1

        business_role_reconciliation = {
            "持仓ETF": holding_etfs,
            "观察ETF": observation_etfs,
            "机会ETF": opportunity_etfs,
            "全市场机会发现": discovery_summary,
        }

    main_answer = answers["MAIN_CANDIDATE"]
    risk_answer = answers["RISK_PERMISSION"]
    risk_permission = _normalize_risk_permission(risk_answer["final_action"])
    if not risk_permission:
        raise ValueError("decision response RISK_PERMISSION.final_action has no registered formal permission")
    raw_main_opportunity_status = str(main_answer["opportunity_status"]).strip()
    main_opportunity_status = _normalize_opportunity_status(raw_main_opportunity_status)
    if raw_main_opportunity_status == "无新增交易机会" and (
        str(main_answer.get("candidate_name") or "").strip() != "现金" or new_amount != 0
    ):
        raise ValueError("无新增交易机会 is only valid for cash-selected zero-new-capital decisions")
    state_problem_map = {
        "现金": "DEPLOYABLE_CASH",
        "全部实际持仓继续占资": "RELEASABLE_CAPITAL",
        "全部持仓ETF追加": "CONCENTRATION_COMMON_RISK",
        "全部正式观察ETF": "CONCENTRATION_COMMON_RISK",
        "12只Discovery临时评估对象": "CONCENTRATION_COMMON_RISK",
        "561980条件释放资本": "RELEASABLE_CAPITAL",
        "159981下一节点Confirm": "TRIAL_CONFIRM_CAPACITY",
    }
    if role_contract_v1:
        forbidden_aggregate_states = [
            str(x or "").strip() for x in compared_capital_states
            if not isinstance(x, dict) and ("观察ETF" in str(x or "") or "Discovery" in str(x or "") or "全市场机会发现" in str(x or ""))
        ]
        if forbidden_aggregate_states:
            raise ValueError(
                "business-role V1 capital competition cannot use observation/discovery aggregates as capital states: "
                + ",".join(forbidden_aggregate_states)
            )
    canonical_capital_states = []
    for raw_state in compared_capital_states:
        if isinstance(raw_state, dict):
            canonical_capital_states.append(raw_state)
            continue
        state_name = str(raw_state or "").strip()
        if not state_name:
            raise ValueError("NEXT_UNIT_CAPITAL_USE compared_capital_states contains empty state")
        problem_id = state_problem_map.get(state_name)
        if not problem_id:
            if "现金" in state_name:
                problem_id = "DEPLOYABLE_CASH"
            elif "释放" in state_name:
                problem_id = "RELEASABLE_CAPITAL"
            elif "Confirm" in state_name or "Trial" in state_name:
                problem_id = "TRIAL_CONFIRM_CAPACITY"
            elif "追加" in state_name or "观察" in state_name or "Discovery" in state_name:
                problem_id = "CONCENTRATION_COMMON_RISK"
            else:
                raise ValueError(f"NEXT_UNIT_CAPITAL_USE capital state has no business-semantics owner: {state_name}")
        owner = answers.get(problem_id) or {}
        action = str(owner.get("final_action") or "").strip()
        comparison = str(owner.get("capital_comparison") or "").strip()
        if not action or not comparison:
            raise ValueError(f"decision response missing capital-state business semantics: {state_name}->{problem_id}")
        selected = state_name in str(next_answer.get("final_action") or "")
        canonical_capital_states.append({
            "state_name": state_name,
            "capital_action": action,
            "remaining_deployable_cash": post_cash,
            "why_not_selected": "已选中" if selected else comparison,
            "opportunity_cost_if_selected": str(next_answer.get("cash_opportunity_cost") or comparison),
        })

    _validate_material_state_transitions(
        answers, work_package,
        risk_permission=risk_permission,
        candidate_code=str(main_answer.get("candidate_code") or ""),
        opportunity_status=main_opportunity_status,
        position_reviews=position_reviews,
    )

    projected = json.loads(json.dumps(source))
    projected.update({
        "risk_permission": risk_permission,
        "candidate_code": str(main_answer.get("candidate_code") or ""),
        "candidate_name": str(main_answer["candidate_name"]),
        "main_candidate": (
            f"{main_answer['candidate_name']}（{main_answer['candidate_code']}）"
            if str(main_answer.get("candidate_code") or "").strip()
            else str(main_answer["candidate_name"])
        ),
        "opportunity_status": main_opportunity_status,
        "lifecycle": lifecycle,
        "managed_position_reviews": position_reviews,
        "etf_opportunity_reviews": opportunity_reviews,
        "observation_eligibility_reviews": observation_eligibility_reviews,
        "observation_management": observation_management,
        "business_role_reconciliation": business_role_reconciliation,
        "continued_holding_opportunity_cost": "；".join(str(x.get("continued_holding_opportunity_cost") or "") for x in position_reviews),
        "action_changes_now": any(bool(x.get("action_changes_now")) for x in position_reviews) or new_amount > 0,
        "next_change_condition": next_answer["next_change_condition"],
        "next_unit_capital_use": next_answer["final_action"],
        "capital_competition": {
            "next_unit_capital_use": next_answer["final_action"],
            "full_competition_completed": True,
            "releasable_capital_reviewed": True,
            "post_action_deployable_cash": post_cash,
            "future_opportunity_capacity": next_answer["future_opportunity_capacity"],
            "cash_opportunity_cost": next_answer["cash_opportunity_cost"],
            "alternative_capital_use_review": next_answer["alternative_capital_use_review"],
            "concentration_account_structure_effect": next_answer["concentration_account_structure_effect"],
            "selected_state_reason": next_answer["selected_state_reason"],
            "new_amount_yuan": new_amount,
            "zero_amount_decisive_reason": zero_amount_decisive_reason,
            "compared_capital_states": canonical_capital_states,
            "held_etf_add_capital_reviews": held_add_reviews,
            "etf_opportunity_reviews": capital_opportunity_reviews,
        },
    })
    projected["decision_evidence_consumption"] = {**consumed,
        "discovery_to_capital_competition_consumed": True,
        "all_managed_positions_sell_chain_consumed": bool(position_reviews),
        "held_etf_additional_capital_consumed": bool(held_add_reviews) or not any(str(x.get("problem_id") or "").startswith("HELD_ETF_ADD:") for x in graph),
        "next_unit_capital_use_consumed": "NEXT_UNIT_CAPITAL_USE" in required_ids}
    projected["capital_use"] = {
        "business_response_projection": True,
        "position_capital_states": {x["security_code"]: x["capital_use"]["position_capital_states"] for x in position_reviews},
        "capital_destinations": {x["security_code"]: x["capital_use"]["capital_destination"] for x in position_reviews},
    }
    return projected

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
    parent_request_id = str(source.get("parent_request_id") or source.get("request_id") or "").strip()
    consumption_error = validate_decision_evidence_consumption(
        source.get("decision_evidence_consumption"),
        parent_request_id=parent_request_id,
    )
    if consumption_error:
        raise ValueError(consumption_error)
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
