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
            "category": "NODE_LOCAL_CANDIDATE" if pid.startswith("DISCOVERY:") else "OBSERVED_ETF",
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
