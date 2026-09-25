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
    """Project only response/evidence relationships; never invent an investment judgment."""
    if not isinstance(response, dict) or not isinstance(work_package, dict):
        raise ValueError("decision response/work package must be objects")
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("decision response requires answers keyed by problem_id")
    required = [str(x.get("problem_id") or "") for x in (work_package.get("problem_graph") or []) if x.get("problem_id")]
    missing = [pid for pid in required if pid not in answers]
    if missing:
        raise ValueError("decision response missing problem_ids: " + ",".join(missing))
    for pid, answer in answers.items():
        if not isinstance(answer, dict):
            raise ValueError(f"decision response answer must be an object: {pid}")
        for field in ("final_action", "capital_comparison", "next_change_condition", "evidence_decision_impact"):
            if answer.get(field) in (None, "", [], {}):
                raise ValueError(f"decision response missing {pid}.{field}")
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
    for key, values in domains.items():
        consumed[key] = values
    consumed.update({
        "discovery_to_capital_competition_consumed": True,
        "all_managed_positions_sell_chain_consumed": True,
        "held_etf_additional_capital_consumed": True,
        "next_unit_capital_use_consumed": True,
    })
    projected = json.loads(json.dumps(source))
    projected["decision_evidence_consumption"] = consumed
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
