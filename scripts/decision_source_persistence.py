from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SOURCE_REQUEST_TYPE = "BUSINESS_DECISION_SOURCE"
SOURCE_CLASSIFICATION = "BUSINESS_DECISION_SOURCE"
REPLY_READY = "REPLY_READY"
BUSINESS_REQUIRED_FIELDS = (
    "risk_permission",
    "main_candidate",
    "opportunity_status",
    "managed_position_reviews",
    "etf_opportunity_reviews",
    "capital_competition",
    "capital_use",
    "continued_holding_opportunity_cost",
    "higher_efficiency_alternative",
    "action_changes_now",
    "next_change_condition",
    "deployable_capital",
    "releasable_capital",
    "new_amount",
    "funding_source",
    "destination",
    "post_action_capital",
    "future_trial_confirm_capacity",
    "concentration_common_risk",
    "next_unit_capital_use",
    "decisive_reasons",
)

def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def fingerprint(source: dict) -> str:
    body = {k: v for k, v in source.items() if k not in {"fingerprint", "recorded_at_beijing", "status"}}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

def classify_source(request: dict) -> str:
    if str(request.get("request_type") or "").upper() == SOURCE_REQUEST_TYPE:
        return SOURCE_CLASSIFICATION
    return ""

def validate_phase1_source(request: dict) -> str:
    if classify_source(request) != SOURCE_CLASSIFICATION:
        return "explicit BUSINESS_DECISION_SOURCE request_type required"
    if not str(request.get("request_id") or "").strip():
        return "request_id required"
    if not str(request.get("parent_request_id") or request.get("request_id") or "").strip():
        return "parent request binding required"
    if not str(request.get("consumed_snapshot") or request.get("consumed_snapshot_path") or "").strip():
        return "request-bound PIT snapshot required"
    decision = request.get("business_decision") or request.get("formal_decision")
    if not isinstance(decision, dict) or not decision:
        return "business_decision required"
    missing = [field for field in BUSINESS_REQUIRED_FIELDS if field not in decision]
    if missing:
        return "missing business judgment: " + ",".join(missing)
    for field in ("opportunity_status", "capital_use", "continued_holding_opportunity_cost", "action_changes_now", "next_change_condition"):
        value = decision.get(field)
        if value is None or value == "" or value == [] or value == {}:
            return "ambiguous business judgment: " + field
    return ""

def build_formal_completion_from_source(source: dict) -> dict:
    error = validate_phase1_source(source)
    if error:
        raise ValueError(error)
    decision = dict(source.get("business_decision") or source.get("formal_decision") or {})
    projected = dict(source)
    projected["request_type"] = "STATE_SYNC_ONLY"
    projected["source"] = "CHATGPT_BUSINESS_DECISION_SOURCE_PROJECTION"
    projected["formal_decision"] = decision
    projected["source_classification"] = SOURCE_CLASSIFICATION
    projected["projection_status"] = "FORMAL_COMPLETION_PROJECTED"
    return projected

def persist_source(root: Path, request: dict) -> dict:
    error = validate_phase1_source(request)
    if error:
        raise ValueError(error)
    source_id = str(request["request_id"]).strip()
    source_dir = root / "events" / "decision_sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / f"{source_id}.json"
    body = dict(request)
    body["request_type"] = SOURCE_REQUEST_TYPE
    body["source_classification"] = SOURCE_CLASSIFICATION
    body["fingerprint"] = fingerprint(body)
    body["status"] = REPLY_READY
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("fingerprint") != body["fingerprint"]:
            raise ValueError("semantic conflict for immutable decision source")
        return {"path": str(path), "source_durable": True, "reply_ready": True, "idempotent_reuse": True, "fingerprint": body["fingerprint"]}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(path), "source_durable": True, "reply_ready": True, "idempotent_reuse": False, "fingerprint": body["fingerprint"]}
