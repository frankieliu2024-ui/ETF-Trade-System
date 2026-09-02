"""Canonical review-prerequisite lifecycle projection.

This module consumes recovery evidence; it never invents market facts or writes
state.  A terminal projection is valid only when the canonical recovery
assessment proves the prerequisite is unrecoverable.
"""
from __future__ import annotations

from typing import Any

try:
    from historical_market_fact_recovery import RecoveryClass
except ModuleNotFoundError:
    from scripts.historical_market_fact_recovery import RecoveryClass

TERMINAL_STATUS = "REVIEW_UNAVAILABLE_UNRECOVERABLE"
TERMINAL_EVENT_TYPE = "FORMAL_POST_CLOSE_REVIEW_UNAVAILABLE"
_REQUIRED = (
    "trade_event_id",
    "review_target_market_date",
    "missing_prerequisite",
    "recovery_target",
    "recovery_classification",
    "recovery_attempted",
    "provider",
    "source_reference",
    "historical_retrieved_at_beijing",
    "effective_market_time_beijing",
    "required_coverage",
    "actual_coverage",
    "unrecoverable_reason",
    "evidence_reference",
)


def validate_unrecoverable_assessment(assessment: dict[str, Any], event_id: str) -> tuple[bool, str]:
    if not isinstance(assessment, dict):
        return False, "assessment_missing"
    if str(assessment.get("trade_event_id") or "") != event_id:
        return False, "trade_event_id_mismatch"
    if str(assessment.get("recovery_classification") or "").upper() != RecoveryClass.UNRECOVERABLE.value:
        return False, "recovery_classification_not_unrecoverable"
    for key in _REQUIRED:
        value = assessment.get(key)
        if value in (None, "", [], {}):
            return False, f"missing_{key}"
    if assessment.get("recovery_attempted") is not True:
        return False, "recovery_not_attempted"
    if assessment.get("original_provider_observation_time_beijing") not in (None, ""):
        return False, "unavailable_fact_must_not_claim_observation_time"
    required = assessment.get("required_coverage")
    actual = assessment.get("actual_coverage")
    if not isinstance(required, dict) or not isinstance(actual, dict):
        return False, "coverage_must_be_structured"
    if int(actual.get("count") or 0) >= int(required.get("count") or 0) and int(required.get("count") or 0) > 0:
        return False, "complete_coverage_cannot_be_unrecoverable"
    return True, ""


def build_unrecoverable_review_event(
    assessment: dict[str, Any], *, request_id: str, created_at_beijing: str
) -> dict[str, Any]:
    ok, reason = validate_unrecoverable_assessment(
        assessment, str(assessment.get("trade_event_id") or "")
    )
    if not ok:
        raise ValueError(reason)
    return {
        "event_type": TERMINAL_EVENT_TYPE,
        "schema_version": "1.0",
        "trade_event_id": assessment["trade_event_id"],
        "market_date": assessment["review_target_market_date"],
        "request_id": request_id,
        "updated_at_beijing": created_at_beijing,
        "review_unavailability": {
            **assessment,
            "lifecycle_status": TERMINAL_STATUS,
            "normal_review_eligible": False,
            "normal_case_generated": False,
            "formal_case_mapping_generated": False,
        },
        "safety_boundary": (
            "真实成交事实保留；仅记录canonical recovery证明的复盘前置事实不可恢复；"
            "不生成normal formal review、CASE结论或交易动作。"
        ),
    }


def is_valid_unrecoverable_review_event(event: dict[str, Any], event_id: str) -> bool:
    if not isinstance(event, dict) or event.get("event_type") != TERMINAL_EVENT_TYPE:
        return False
    assessment = event.get("review_unavailability")
    if not isinstance(assessment, dict):
        return False
    ok, _ = validate_unrecoverable_assessment(assessment, event_id)
    return ok and assessment.get("lifecycle_status") == TERMINAL_STATUS


def can_reopen_normal_review(event: dict[str, Any], event_id: str) -> bool:
    return not is_valid_unrecoverable_review_event(event, event_id)


def terminal_experience_line(assessment: dict[str, Any]) -> str:
    return (
        f"- {assessment['trade_event_id']}｜正式盘后复盘不可完成：所需历史市场事实经canonical recovery "
        f"判定为{assessment['recovery_classification']}；未生成正常CASE结论；"
        f"目标={assessment['review_target_market_date']}/{assessment['recovery_target']}；"
        f"证据={assessment['evidence_reference']}；来源={assessment['provider']}。"
    )
