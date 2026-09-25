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
    missing = [key for key in required if key not in value or value[key] in (None, "", [], {})]
    if missing:
        return "decision_evidence_consumption missing: " + ",".join(missing)
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


def project_decision_response(source: dict[str, Any], response: dict[str, Any], work_package: dict[str, Any]) -> dict[str, Any]:
    """Project actor-only business answers into the canonical Business Decision Source."""
    if not isinstance(response, dict) or not isinstance(work_package, dict):
        raise ValueError("decision response/work package must be objects")
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("decision response requires answers keyed by problem_id")
    graph = [x for x in (work_package.get("problem_graph") or []) if isinstance(x, dict)]
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
            if str(answer.get("final_action") or "").upper() in {"REDUCE", "EXIT"}:
                if answer.get("quantity") in (None, "") or not answer.get("capital_destination"):
                    raise ValueError(f"decision response missing action quantity/destination: {pid}")
        if pid == "MAIN_CANDIDATE":
            for field in ("candidate_code", "candidate_name", "opportunity_status"):
                if answer.get(field) in (None, ""):
                    raise ValueError(f"decision response missing MAIN_CANDIDATE.{field}")
        if pid.startswith(("DISCOVERY:", "OBSERVATION:")):
            if str(answer.get("opportunity_status") or "").strip() not in {"无机会", "观察机会", "Trial机会", "Confirm机会"}:
                raise ValueError(f"decision response missing registered opportunity_status: {pid}")
            if str(answer.get("disposition") or "").upper() not in {"ADMIT", "REJECT", "RETAIN", "EXIT"}:
                raise ValueError(f"decision response missing valid opportunity disposition: {pid}")
            if not str(answer.get("reason") or "").strip():
                raise ValueError(f"decision response missing opportunity reason: {pid}")

    next_answer = answers.get("NEXT_UNIT_CAPITAL_USE") or {}
    for field in (
        "new_amount_yuan", "post_action_deployable_cash", "future_opportunity_capacity",
        "cash_opportunity_cost", "alternative_capital_use_review",
        "concentration_account_structure_effect", "selected_state_reason", "compared_capital_states",
    ):
        if next_answer.get(field) in (None, "", []):
            raise ValueError(f"decision response missing NEXT_UNIT_CAPITAL_USE.{field}")
    try:
        new_amount = float(next_answer.get("new_amount_yuan"))
        post_cash = float(next_answer.get("post_action_deployable_cash"))
    except (TypeError, ValueError):
        raise ValueError("NEXT_UNIT_CAPITAL_USE amount/cash must be numeric")
    if new_amount < 0 or post_cash < 0:
        raise ValueError("NEXT_UNIT_CAPITAL_USE amount/cash must be non-negative")
    if new_amount == 0 and not str(next_answer.get("zero_amount_decisive_reason") or "").strip():
        raise ValueError("decision response requires zero_amount_decisive_reason when new amount is zero")

    layer_map = {"A_SHARE_STYLE_FEEDBACK": "layer_2_a_share_internal", "ETF_RELATIVE_STRENGTH": "layer_3_etf_opportunity_capital"}
    consumed = {"request_id": str(source.get("parent_request_id") or source.get("request_id") or "").strip()}
    domains = {"layer_1_external_cross_market": [], "layer_2_a_share_internal": [], "layer_3_etf_opportunity_capital": []}
    for requirement in work_package.get("evidence_requirements") or []:
        pid = requirement.get("target_problem_id")
        answer = answers.get(pid) or {}
        impact = answer.get("evidence_decision_impact") or []
        if requirement.get("required") and not impact:
            raise ValueError(f"decision response missing evidence impact: {pid}")
        evidence_id = requirement.get("requirement_id")
        domain = layer_map.get(requirement.get("evidence_class"), "layer_1_external_cross_market")
        if evidence_id in impact or impact == ["ALL_REQUIRED"]:
            domains[domain].append(evidence_id)
    if any(not domains[k] for k in domains):
        raise ValueError("decision response must consume evidence in all three decision layers")
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
        action = str(answer.get("final_action") or "").upper()
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
            "opportunity_status": answer.get("opportunity_status"),
            "conclusion": answer.get("final_action"), "reason": answer.get("reason"),
        })
        if pid.startswith("DISCOVERY:"):
            if disposition not in {"ADMIT", "REJECT"}:
                raise ValueError(f"Discovery disposition must be ADMIT/REJECT: {pid}")
            observation_eligibility_reviews.append({"code": code, "disposition": disposition, "reason": answer.get("reason")})
            if disposition == "ADMIT":
                observation_management.append({"code": code, "action": "ADMIT", "reason": answer.get("reason")})
        else:
            if disposition not in {"RETAIN", "EXIT"}:
                raise ValueError(f"Observation disposition must be RETAIN/EXIT: {pid}")
            observation_management.append({"code": code, "action": disposition, "reason": answer.get("reason")})

    main_answer = answers["MAIN_CANDIDATE"]
    risk_answer = answers["RISK_PERMISSION"]
    projected = json.loads(json.dumps(source))
    projected.update({
        "risk_permission": risk_answer["final_action"],
        "candidate_code": str(main_answer["candidate_code"]),
        "candidate_name": str(main_answer["candidate_name"]),
        "main_candidate": f"{main_answer['candidate_name']}（{main_answer['candidate_code']}）",
        "opportunity_status": main_answer["opportunity_status"],
        "lifecycle": lifecycle,
        "managed_position_reviews": position_reviews,
        "etf_opportunity_reviews": opportunity_reviews,
        "observation_eligibility_reviews": observation_eligibility_reviews,
        "observation_management": observation_management,
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
            "zero_amount_decisive_reason": next_answer.get("zero_amount_decisive_reason"),
            "compared_capital_states": next_answer["compared_capital_states"],
            "held_etf_add_capital_reviews": held_add_reviews,
            "etf_opportunity_reviews": opportunity_reviews,
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
