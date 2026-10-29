from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    from runtime_session_gate import classify_live_snapshot_request
    from task_runtime_identity import derive_runtime_identity, derive_scheduled_review_identity
    from process_state_sync_request import (
        validate_current_lifecycle_contract,
        validate_managed_position_lifecycle,
    )
    from user_visible_presentation import validate_user_visible_content
except ModuleNotFoundError:
    from scripts.runtime_session_gate import classify_live_snapshot_request
    from scripts.task_runtime_identity import derive_runtime_identity, derive_scheduled_review_identity
    from scripts.process_state_sync_request import (
        validate_current_lifecycle_contract,
        validate_managed_position_lifecycle,
    )
    from scripts.user_visible_presentation import validate_user_visible_content

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
REQUEST_DIR = ROOT / "requests" / "live_snapshot"
REQUIRED_MIDDAY_FIELDS = (
    "market_date",
    "review_scope",
    "review_version",
    "reviewed_at_beijing",
    "market_phase",
    "data_time",
    "account_change_status",
    "trade_status",
    "holdings",
    "holding_actions",
    "discovery_status",
    "observation_count",
    "opportunity_count",
    "deployable_cash_yuan",
    "new_capital_yuan",
    "releasable_capital_yuan",
    "action",
    "review_boundary",
)

REQUIRED_FULL_DAY_FIELDS = (
    "market_date",
    "review_scope",
    "review_version",
    "reviewed_at_beijing",
    "risk_permission",
    "opportunity_status",
    "main_candidate",
    "lifecycle",
    "holding_actions",
    "amount_yuan",
    "action",
    "data_time",
    "account_close",
    "capital_efficiency",
    "capital_efficiency_reason",
    "next_unit_capital_use",
    "max_risk",
    "error_reason",
    "information_gap",
    "most_fragile_hypothesis",
    "overseas_overestimate",
    "overseas_a_share_divergence",
    "case_mode",
    "review_boundary",
)


def _safe_id(value: object) -> str:
    value = str(value or "").strip()
    if not value or any(part in value for part in ("/", "\\", "..")):
        raise ValueError("scheduled review completion requires a safe request identity")
    return value


def validate_full_day_review(review: dict, account: dict | None = None) -> None:
    if not isinstance(review, dict) or not review:
        raise ValueError("completed formal_review payload is required")
    if str(review.get("review_scope") or "").strip().upper() != "FULL_DAY":
        raise ValueError("scheduled full-day completion requires review_scope=FULL_DAY")
    missing = [key for key in REQUIRED_FULL_DAY_FIELDS if key not in review or review[key] in (None, "")]
    if missing:
        raise ValueError("formal_review missing required full-day fields: " + ", ".join(missing))
    data_time = review.get("data_time")
    if not isinstance(data_time, dict) or not str(data_time.get("close_snapshot") or "").strip():
        raise ValueError("formal_review.data_time.close_snapshot is required")
    if not isinstance(review.get("account_close"), dict):
        raise ValueError("formal_review.account_close must be an object")
    if not isinstance(review.get("lifecycle"), dict) or not isinstance(review.get("holding_actions"), dict):
        raise ValueError("formal_review lifecycle and holding_actions must be objects")
    if account is not None:
        lifecycle_error = validate_current_lifecycle_contract(review.get("lifecycle"), account)
        if lifecycle_error:
            raise ValueError(f"invalid formal review lifecycle contract: {lifecycle_error}")
        managed_error = validate_managed_position_lifecycle(
            review.get("holding_actions"), account, "formal_review.managed_positions"
        )
        if managed_error:
            raise ValueError(f"invalid formal review managed-position contract: {managed_error}")


def validate_midday_review(review: dict, account: dict | None = None) -> None:
    """Validate the frozen morning-stage review without requiring a 15:00 close."""
    if not isinstance(review, dict) or not review:
        raise ValueError("completed formal_review payload is required")
    if str(review.get("review_scope") or "").strip().upper() != "MORNING_SESSION_STAGE_ONLY":
        raise ValueError("scheduled midday completion requires review_scope=MORNING_SESSION_STAGE_ONLY")
    missing = [key for key in REQUIRED_MIDDAY_FIELDS if key not in review or review[key] in (None, "")]
    if missing:
        raise ValueError("formal_review missing required midday fields: " + ", ".join(missing))
    data_time = review.get("data_time")
    if not isinstance(data_time, dict):
        raise ValueError("formal_review.data_time must be an object")
    if not str(data_time.get("a_share_fact_boundary_beijing") or "").strip():
        raise ValueError("formal_review.data_time.a_share_fact_boundary_beijing is required")
    if not str(data_time.get("market_snapshot") or "").strip():
        raise ValueError("formal_review.data_time.market_snapshot is required")
    if not isinstance(review.get("holdings"), (dict, list)):
        raise ValueError("formal_review.holdings must be an object or list")
    if not isinstance(review.get("holding_actions"), dict):
        raise ValueError("formal_review.holding_actions must be an object")



def validate_formal_object_coverage(review: dict, account: dict | None = None) -> dict:
    """Return the auditable object-coverage summary for a frozen trade review.
    
    The review validators above remain the authority for required fields and
    lifecycle rules. This helper only records the coverage actually carried by
    the completion, so the shared Scheduled Review builder cannot fail at
    runtime because of a missing coverage helper.
    """
    review = review if isinstance(review, dict) else {}
    holdings = review.get("holdings")
    holding_actions = review.get("holding_actions")
    if not isinstance(holding_actions, dict):
        holding_actions = {}
    holding_count = len(holdings) if isinstance(holdings, (dict, list)) else 0
    action_count = len(holding_actions)
    account_positions = 0
    if isinstance(account, dict):
        positions = account.get("positions") or account.get("holdings") or []
        if isinstance(positions, (dict, list)):
            account_positions = len(positions)
    return {
        "status": "COMPLETE",
        "review_scope": str(review.get("review_scope") or "").strip(),
        "holding_object_count": holding_count,
        "holding_action_count": action_count,
        "account_position_count": account_positions,
        "coverage_source": "FROZEN_TRADE_REVIEW",
        "read_only": True,
    }


def build_trade_review_completion_request(
    formal_review: dict,
    request_id: str,
    requested_at_beijing: str,
    market_date: str = "",
    parent_request_id: str = "",
    account: dict | None = None,
    final_content: str = "",
    task_id: str = "",
    task_run_id: str = "",
    occurrence_id: str = "",
    entry_mode: str = "",
) -> dict:
    """Wrap a frozen Scheduled Review for the legacy state-sync compatibility path."""
    request_id, task_run_id, occurrence_id = derive_scheduled_review_identity(
        task_id or "ETF交易复盘", requested_at_beijing, entry_mode,
        request_id, task_run_id, occurrence_id,
    )
    scope = str((formal_review or {}).get("review_scope") or "").strip().upper()
    is_midday = scope == "MORNING_SESSION_STAGE_ONLY"
    review_version = str((formal_review or {}).get("review_version") or "").upper()
    case_mode = str((formal_review or {}).get("case_mode") or "").upper()
    is_weekly = "WEEKLY_REVIEW" in review_version or "WEEKLY_REVIEW" in case_mode
    if is_midday:
        validate_midday_review(formal_review, account)
    else:
        validate_full_day_review(formal_review, account)
    coverage = validate_formal_object_coverage(formal_review, account)
    validate_user_visible_content(str(final_content or ""), canonical_closure_confirmed=False)
    effective_entry_mode = str(entry_mode or "").strip().upper()
    if effective_entry_mode not in {"MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"}:
        raise ValueError("scheduled trade review completion requires a registered entry_mode")
    effective_task_run_id = _safe_id(task_run_id or request_id)
    effective_occurrence_id = _safe_id(occurrence_id or effective_task_run_id)
    frozen_content = str(final_content or "")
    frozen_content_hash = hashlib.sha256(frozen_content.encode("utf-8")).hexdigest()
    review_market_date = str(formal_review.get("market_date") or "").strip()
    effective_market_date = str(market_date or review_market_date).strip()
    if not effective_market_date or effective_market_date != review_market_date:
        raise ValueError("scheduled review completion market_date must match formal_review.market_date")
    payload = {
        "request_id": _safe_id(request_id),
        "request_type": "SCHEDULED_REVIEW_COMPLETION",
        "formal_fact_type": "FORMAL_SCHEDULED_TRADE_REVIEW",
        "source": "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER",
        "interaction_scenario": "SCHEDULED_TRADE_REVIEW",
        "market_phase": str(formal_review.get("market_phase") or ("MIDDAY_BREAK" if is_midday else "POST_CLOSE")),
        "requested_at_beijing": str(requested_at_beijing or "").strip(),
        "market_date": effective_market_date,
        "entry_mode": effective_entry_mode,
        "task_run_id": effective_task_run_id,
        "occurrence_id": effective_occurrence_id,
        "formal_review": formal_review,
        "formal_object_coverage": coverage,
        "presentation_binding": {
            "report_type": "ETF_TRADE_REVIEW",
            "task_id": str(task_id or "ETF交易复盘"),
            "task_run_id": effective_task_run_id,
            "occurrence_id": effective_occurrence_id,
            "entry_mode": effective_entry_mode,
            "request_id": str(request_id),
            "report_id": f"ETF_TRADE_REVIEW:{effective_task_run_id}",
            "full_content": frozen_content,
            "content_hash": frozen_content_hash,
            "content_hash_source": "completion_builder",
        },
    }
    if parent_request_id:
        payload["parent_request_id"] = _safe_id(parent_request_id)
    if not payload["requested_at_beijing"]:
        raise ValueError("scheduled review completion requires requested_at_beijing")
    # This is the public Scheduled Review completion contract. The ingress
    # classifier may route it internally to the existing state-sync owner, but
    # the request itself must never be downgraded to the legacy
    # request_type=STATE_SYNC_ONLY contract.
    if str(payload.get("request_type") or "").upper() != "SCHEDULED_REVIEW_COMPLETION":
        raise ValueError("scheduled review completion must use request_type=SCHEDULED_REVIEW_COMPLETION")
    if classify_live_snapshot_request(payload) != "STATE_SYNC_ONLY":
        raise ValueError("scheduled review completion cannot reach the canonical state-sync owner")
    return payload




def validate_formal_decision_fact_binding(formal_review: dict, request_id: str) -> None:
    """Fail closed unless the frozen decision names the completed same-request fact chain."""
    binding = formal_review.get("fact_binding")
    if not isinstance(binding, dict):
        binding = formal_review.get("request_binding")
    if not isinstance(binding, dict):
        raise ValueError("formal decision requires a same-request fact_binding before completion")
    expected = str(request_id or "").strip()
    checks = {
        "request_id": str(binding.get("request_id") or "").strip() == expected,
        "market_snapshot_status": str(binding.get("market_snapshot_status") or "").upper() in {"CONSUMED", "COMPLETE", "READY"},
        "fact_chain_status": str(binding.get("fact_chain_status") or "").upper() in {"COMPLETE", "READY"},
        "query_context_status": str(binding.get("query_context_status") or "").upper() in {"BOUND", "COMPLETE", "READY"},
        "decision_context_status": str(binding.get("decision_context_status") or "").upper() in {"BOUND", "COMPLETE", "READY"},
        "objects_status": str(binding.get("objects_status") or "").upper() in {"BOUND", "COMPLETE", "READY"},
        "gate_status": str(binding.get("gate_status") or "").upper() in {"READY", "REPLY_FREEZABLE"},
    }
    failed = [key for key, ok in checks.items() if not ok]
    if failed:
        raise ValueError("formal decision same-request fact chain is not complete: " + ", ".join(failed))


def validate_formal_decision_presentation_timestamp(final_content: str, requested_at_beijing: str) -> None:
    """Reject a frozen Formal Decision body copied from another run."""
    content = str(final_content or "")
    requested = str(requested_at_beijing or "").strip()
    if not requested:
        raise ValueError("formal decision completion requires requested_at_beijing")
    request_minute = requested[:16].replace("T", " ")
    first_line = content.splitlines()[0].strip() if content.splitlines() else ""
    expected_prefix = f"【ETF正式决策】{request_minute} "
    if not first_line.startswith(expected_prefix):
        raise ValueError("formal decision presentation title does not match current request timestamp")
    if not first_line.endswith(("正式判断", "降级判断", "超时判断")):
        raise ValueError("formal decision presentation title has invalid decision path")


def build_formal_review_completion_request(
    formal_review: dict,
    request_id: str,
    requested_at_beijing: str,
    market_date: str = "",
    parent_request_id: str = "",
    account: dict | None = None,
    final_content: str = "",
    task_id: str = "ETF正式决策",
    task_run_id: str = "",
    occurrence_id: str = "",
    entry_mode: str = "",
) -> dict:
    """Build a Formal Decision completion on the shared review contract.

    This is the new production path. It deliberately contains no durable
    business-source envelope and no BDS path. The investment judgment is the
    frozen presentation carried by the task-direct GitHub Contents writer
    written directly to GitHub Contents by the live task; only the report identity and fact scope differ.
    """
    if str(task_id or "").strip() != "ETF正式决策":
        raise ValueError("formal review completion requires canonical task_id")
    if not isinstance(formal_review, dict) or not formal_review:
        raise ValueError("formal review payload is required")
    scope = str(formal_review.get("review_scope") or "").strip().upper()
    if scope == "MORNING_SESSION_STAGE_ONLY":
        validate_midday_review(formal_review, account)
    else:
        validate_full_day_review(formal_review, account)
    validate_formal_decision_fact_binding(formal_review, request_id)
    validate_user_visible_content(str(final_content or ""), canonical_closure_confirmed=False)
    validate_formal_decision_presentation_timestamp(final_content, requested_at_beijing)
    effective_entry_mode = str(entry_mode or "").strip().upper()
    if effective_entry_mode not in {"MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"}:
        raise ValueError("formal review completion requires a registered entry mode")
    effective_task_run_id = _safe_id(task_run_id or request_id)
    effective_occurrence_id = _safe_id(occurrence_id or effective_task_run_id)
    effective_market_date = str(market_date or formal_review.get("market_date") or "").strip()
    if effective_market_date != str(formal_review.get("market_date") or "").strip():
        raise ValueError("formal review completion market_date must match formal_review")
    frozen_content = str(final_content or "")
    frozen_hash = hashlib.sha256(frozen_content.encode("utf-8")).hexdigest()
    effective_parent_request_id = _safe_id(parent_request_id or request_id)
    decision_id = f"ETF_FORMAL_DECISION:{effective_task_run_id}"
    report_id = f"ETF_FORMAL_DECISION:{effective_task_run_id}"
    payload = {
        "request_id": _safe_id(request_id),
        "request_type": "SCHEDULED_REVIEW_COMPLETION",
        "formal_fact_type": "FORMAL_CURRENT_MAIN_DECISION",
        "source": "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER",
        "interaction_scenario": "FORMAL_DECISION",
        "formal_entry_source": str(formal_review.get("source") or "").strip(),
        "entry_mode": effective_entry_mode,
        "task_id": "ETF正式决策",
        "task_run_id": effective_task_run_id,
        "occurrence_id": effective_occurrence_id,
        "parent_request_id": effective_parent_request_id,
        "decision_id": decision_id,
        "requested_at_beijing": str(requested_at_beijing or "").strip(),
        "market_date": effective_market_date,
        "formal_review": formal_review,
        "presentation_binding": {
            "report_type": "ETF_FORMAL_DECISION",
            "task_id": "ETF正式决策",
            "task_run_id": effective_task_run_id,
            "occurrence_id": effective_occurrence_id,
            "entry_mode": effective_entry_mode,
            "request_id": str(request_id),
            "parent_request_id": effective_parent_request_id,
            "decision_id": decision_id,
            "report_id": report_id,
            "full_content": frozen_content,
            "content_hash": frozen_hash,
            "content_hash_source": "completion_builder",
        },
    }
    if not payload["requested_at_beijing"]:
        raise ValueError("formal review completion requires requested_at_beijing")
    return payload


SYSTEM_REVIEW_FORBIDDEN_AUTHORITY_PHRASES = (
    "继续维持禁止新增",
    "维持禁止新增",
    "继续禁止新增",
    "维持允许新增",
    "继续维持允许新增",
)
SYSTEM_REVIEW_FALSE_ATTRIBUTION_PHRASES = (
    "逐笔归因仍待用户确认",
    "成交归因仍待用户确认",
    "确认能源化工ETF（159981）5,700份买入对应Trial还是Confirm",
)
SYSTEM_REVIEW_TOO_NARROW_REASSESSMENT_PHRASES = (
    "只有出现新的交易需要时才进入正式判断",
    "仅在出现新的交易需要时才进入正式判断",
    "只有出现新成交时才进入正式判断",
    "仅在出现新成交时才进入正式判断",
)
SYSTEM_REVIEW_FACT_CUTOFF_FIELDS = (
    "fact_cutoff_at_beijing",
    "facts_as_of_beijing",
)


def _canonical_attribution_summary(system_review: dict) -> dict:
    summary = (system_review or {}).get("canonical_trade_attribution")
    return summary if isinstance(summary, dict) else {}


def _reject_false_user_attribution_dependency(system_review: dict, content: str) -> None:
    """Validate user-action claims against canonical attribution semantics, not wording.

    The actor supplies the canonical summary it consumed.  A linked decision identity
    makes mapping a machine-known fact even when execution-quality enrichment is PARTIAL.
    Only genuinely missing/ambiguous identities may require user mapping.
    """
    summary = _canonical_attribution_summary(system_review)
    if not summary:
        return
    trades = summary.get("trades") or []
    known = [t for t in trades if str(t.get("decision_identity_status") or "").upper() == "KNOWN"]
    unresolved = [t for t in trades if str(t.get("decision_identity_status") or "").upper() in {"MISSING", "AMBIGUOUS"}]
    user_req = str((system_review or {}).get("user_attribution_requirement") or "NONE").upper()
    if user_req not in {"NONE", "MAPPING_REQUIRED"}:
        raise ValueError("Scheduled System Review user_attribution_requirement must be NONE or MAPPING_REQUIRED")
    if user_req == "MAPPING_REQUIRED" and not unresolved:
        raise ValueError("Scheduled System Review must not request user mapping for canonically linked trades")
    if user_req == "NONE" and unresolved and any(bool(t.get("decision_identity_required")) for t in unresolved):
        raise ValueError("Scheduled System Review must surface genuinely unresolved required trade attribution")
    if known and any(bool(t.get("asks_user_to_confirm_mapping")) for t in known):
        raise ValueError("Scheduled System Review must not request user mapping for canonically linked trades")
    if known and any(bool(t.get("asks_user_to_classify_trial_confirm")) for t in known):
        raise ValueError("Scheduled System Review must resolve linked Trial/Confirm semantics from canonical decision facts")


def _validate_system_review_fact_cutoff(system_review: dict) -> None:
    """Require an explicit coherent fact cutoff when the actor supplies source as-of facts.

    The cutoff freezes what the review claims to know.  Later canonical publications may
    supersede the report, but they do not retroactively mutate or invalidate the frozen
    REPORT.  The actor must final-reread latest main before choosing this cutoff.
    """
    source_as_of = (system_review or {}).get("source_facts_as_of")
    if source_as_of is None:
        return
    if not isinstance(source_as_of, dict) or not source_as_of:
        raise ValueError("Scheduled System Review source_facts_as_of must be a non-empty object")
    cutoff = ""
    for key in SYSTEM_REVIEW_FACT_CUTOFF_FIELDS:
        value = str((system_review or {}).get(key) or "").strip()
        if value:
            cutoff = value
            break
    if not cutoff:
        raise ValueError("Scheduled System Review with source as-of facts requires fact_cutoff_at_beijing")
    advanced = [
        name for name, as_of in source_as_of.items()
        if str(as_of or "").strip() and str(as_of).strip() > cutoff
    ]
    if advanced:
        raise ValueError(
            "Scheduled System Review source fact is newer than frozen fact cutoff: " + ", ".join(sorted(advanced))
        )


def validate_system_review_presentation(system_review: dict, final_content: str) -> None:
    """Keep Scheduled System Review informational and non-decisional.

    Canonical trade facts may carry a linked decision identity while execution-quality
    enrichment remains PARTIAL.  The report must not turn that distinction into a
    false user mapping dependency, and it must not renew prior Formal Decision authority.
    """
    content = str(final_content or "").strip()
    if not content:
        raise ValueError("scheduled system review completion requires frozen final_content")
    if any(phrase in content for phrase in SYSTEM_REVIEW_FORBIDDEN_AUTHORITY_PHRASES):
        raise ValueError("Scheduled System Review must not renew or extend Formal Decision trading authority")
    if any(phrase in content for phrase in SYSTEM_REVIEW_FALSE_ATTRIBUTION_PHRASES):
        raise ValueError("Scheduled System Review must not request user mapping for canonically linked trades")
    if any(phrase in content for phrase in SYSTEM_REVIEW_TOO_NARROW_REASSESSMENT_PHRASES):
        raise ValueError("Scheduled System Review must not make trade need the sole Formal Decision trigger")
    _validate_system_review_fact_cutoff(system_review)
    _reject_false_user_attribution_dependency(system_review, content)
    reconciliation = str((system_review or {}).get("execution_reconciliation") or "").strip().upper()
    if reconciliation in {"CONFIRMATION_REQUIRED", "USER_CONFIRMATION_REQUIRED"}:
        raise ValueError("Scheduled System Review must distinguish execution-quality gaps from missing decision identity")


def build_system_review_completion_request(
    system_review: dict,
    request_id: str,
    requested_at_beijing: str,
    task_id: str,
    task_run_id: str,
    final_content: str,
    entry_mode: str = "AUTOMATIC_SCHEDULED_TASK",
    occurrence_id: str = "",
) -> dict:
    """Build the business completion envelope for one Scheduled System Review occurrence."""
    if not isinstance(system_review, dict) or not system_review:
        raise ValueError("scheduled system review completion requires system_review")
    entry_mode = str(entry_mode or "").strip().upper()
    request_id, task_run_id, occurrence_id = derive_scheduled_review_identity(
        task_id or "ETF系统复核", requested_at_beijing, entry_mode,
        request_id, task_run_id, occurrence_id,
    )
    request_id = _safe_id(request_id)
    if entry_mode not in {"MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"}:
        raise ValueError("scheduled system review completion requires a registered entry_mode")
    task_run_id = _safe_id(task_run_id)
    if not str(requested_at_beijing or "").strip() or not str(final_content or "").strip():
        raise ValueError("scheduled system review completion requires requested_at_beijing and frozen final_content")
    origin_source = str(system_review.get("source") or "").strip()
    origin_mode = str(system_review.get("entry_mode") or "").strip().upper()
    if origin_source or origin_mode:
        if not origin_source or not origin_mode:
            raise ValueError("scheduled system review origin identity is incomplete")
        origin_mapping = {
            "CHATGPT_SCHEDULED_SYSTEM_REVIEW": "AUTOMATIC_SCHEDULED_TASK",
            "CHATGPT_SCHEDULED_SYSTEM_REVIEW_ACTOR": "AUTOMATIC_SCHEDULED_TASK",
            "CHATGPT_MANUAL_SCHEDULED_SYSTEM_REVIEW": "MANUAL_SCHEDULED_TASK",
            "CHATGPT_MANUAL_SYSTEM_REVIEW": "MANUAL_CONVERSATION",
        }
        if origin_source not in origin_mapping or origin_mapping[origin_source] != origin_mode:
            raise ValueError("scheduled system review origin source/entry_mode mismatch")
        if origin_mode != entry_mode:
            raise ValueError("scheduled system review origin entry_mode does not match completion entry_mode")
    validate_system_review_presentation(system_review, final_content)
    validate_user_visible_content(str(final_content), canonical_closure_confirmed=False)
    frozen_content = str(final_content)
    content_hash = hashlib.sha256(frozen_content.encode("utf-8")).hexdigest()
    occurrence_id = _safe_id(occurrence_id)
    return {
        "request_id": request_id,
        "request_type": "SCHEDULED_REVIEW_COMPLETION",
        "formal_fact_type": "FORMAL_SCHEDULED_SYSTEM_REVIEW",
        "source": "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER",
        "interaction_scenario": "SCHEDULED_SYSTEM_REVIEW",
        "entry_mode": entry_mode,
        "requested_at_beijing": str(requested_at_beijing),
        "market_date": str(
            system_review.get("market_date")
            or system_review.get("a_share_latest_formal_market_date")
            or ""
        ),
        "task_id": str(task_id or "ETF系统复核"),
        "task_run_id": task_run_id,
        "occurrence_id": occurrence_id,
        "final_content": frozen_content,
        "report_type": "ETF_SYSTEM_REVIEW",
        "report_id": f"etf_system_review:{task_run_id}",
        "content_hash": content_hash,
        "system_review": system_review,
        "presentation_binding": {
            "report_type": "ETF_SYSTEM_REVIEW",
            "task_id": str(task_id or "ETF系统复核"),
            "task_run_id": task_run_id,
            "occurrence_id": occurrence_id,
            "request_id": request_id,
            "report_id": f"etf_system_review:{task_run_id}",
            "full_content": frozen_content,
            "content_hash": content_hash,
            "content_hash_source": "completion_builder",
        },
    }


def build_report_delivery_from_completion(completion: dict) -> dict:
    """Project the frozen Scheduled Review presentation into the existing REPORT contract."""
    binding = completion.get("presentation_binding") or {}
    report_type = str(binding.get("report_type") or "")
    if report_type not in {"ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW", "ETF_FORMAL_DECISION"}:
        raise ValueError("unsupported Scheduled Review report_type")
    full_content = str(binding.get("full_content") or "")
    task_id = str(binding.get("task_id") or "")
    task_run_id = _safe_id(binding.get("task_run_id"))
    if not full_content or not task_id:
        raise ValueError("Scheduled Review REPORT requires frozen full_content and task_id")
    effective_market_date = str(
        completion.get("market_date")
        or (completion.get("system_review") or {}).get("a_share_latest_formal_market_date")
        or ""
    )
    if not effective_market_date:
        raise ValueError("Scheduled Review REPORT requires effective_market_date")
    content_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
    return {
        "schema_version": "1.0",
        "channel": "REPORT",
        "report_type": report_type,
        "report_id": f"{report_type.lower()}:{task_run_id}",
        "task_id": task_id,
        "task_run_id": task_run_id,
        "generated_at": str(completion.get("requested_at_beijing") or ""),
        "effective_market_date": effective_market_date,
        "source_actor": str(completion.get("source") or "Scheduled Review Completion Owner"),
        "source_reference": f"scheduled-review-completion:{completion.get('request_id')}",
        "title": (
            "【ETF交易复盘】" if report_type == "ETF_TRADE_REVIEW"
            else "【ETF系统复核】" if report_type == "ETF_SYSTEM_REVIEW"
            else "【ETF正式决策】"
        ),
        "summary": "Scheduled Review completed; deliver the frozen FINAL_CONTENT unchanged.",
        "full_content": full_content,
        "content_hash": content_hash,
        "idempotency_key": f"{report_type}:{task_run_id}",
        "delivery_mode": "FULL_REPORT",
        "no_trade_authority": True,
    }


def write_completion_request(
    review_path: str,
    request_id: str,
    requested_at_beijing: str,
    market_date: str = "",
    parent_request_id: str = "",
    output_path: str = "",
    final_content: str = "",
    task_id: str = "",
    task_run_id: str = "",
    occurrence_id: str = "",
    entry_mode: str = "",
) -> Path:
    source = (ROOT / review_path).resolve()
    if ROOT not in source.parents:
        raise ValueError("review path must stay inside repository root")
    review = json.loads(source.read_text(encoding="utf-8"))
    account_path = ROOT / "data/state/account_fact.json"
    account = json.loads(account_path.read_text(encoding="utf-8")) if account_path.exists() else None
    if str(task_id or "").strip() == "ETF系统复核":
        payload = build_system_review_completion_request(
            review,
            request_id,
            requested_at_beijing,
            task_id,
            task_run_id,
            final_content,
            entry_mode=entry_mode or "AUTOMATIC_SCHEDULED_TASK",
            occurrence_id=occurrence_id,
        )
        if market_date and str(payload.get("market_date") or "") != str(market_date):
            raise ValueError("scheduled system review completion market_date must match the review")
    elif str(task_id or "").strip() == "ETF正式决策":
        payload = build_formal_review_completion_request(
            review, request_id, requested_at_beijing, market_date, parent_request_id, account,
            final_content, task_id, task_run_id, occurrence_id, entry_mode
        )
    else:
        payload = build_trade_review_completion_request(
            review, request_id, requested_at_beijing, market_date, parent_request_id, account,
            final_content, task_id, task_run_id, occurrence_id, entry_mode
        )
    target = (ROOT / output_path).resolve() if output_path else REQUEST_DIR / f"{payload['request_id']}__scheduled_review_completion.json"
    if ROOT not in target.parents or target.suffix != ".json":
        raise ValueError("output path must be a repository JSON path")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") == serialized:
            return target
        raise FileExistsError("completion request path already contains a different payload")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(serialized, encoding="utf-8")
    return target


# Historical import compatibility only. New Trade Review production code must
# call the explicitly typed builder above; this alias prevents old replay tools
# from silently creating a second payload shape.
build_completion_request = build_trade_review_completion_request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-review", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--requested-at-beijing", required=True)
    parser.add_argument("--market-date", default="")
    parser.add_argument("--parent-request-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--final-content", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--task-run-id", default="")
    parser.add_argument("--occurrence-id", default="")
    parser.add_argument("--entry-mode", required=True, choices=["MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"])
    args = parser.parse_args()
    target = write_completion_request(
        args.formal_review,
        args.request_id,
        args.requested_at_beijing,
        args.market_date,
        args.parent_request_id,
        args.output,
        args.final_content,
        args.task_id,
        args.task_run_id,
        args.occurrence_id,
        args.entry_mode,
    )
    print(json.dumps({"ok": True, "request": str(target.relative_to(ROOT)).replace("\\", "/")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



def build_formal_runtime_completion_request(
    *,
    run_identity: dict,
    market_date: str,
    final_content: str,
    decision_path: str,
    fact_summary: dict | None = None,
) -> dict:
    """Build Formal Decision completion from current-main facts.

    This handoff identity is not a market-fact request. It carries only the
    already-frozen user-visible content and runtime identity needed by the
    shared Scheduled Review notification chain.
    """
    identity = run_identity if isinstance(run_identity, dict) else {}
    task_run_id = _safe_id(identity.get("task_run_id") or identity.get("occurrence_id"))
    occurrence_id = _safe_id(identity.get("occurrence_id") or task_run_id)
    requested_at = str(identity.get("requested_at_beijing") or "").strip()
    entry_mode = str(identity.get("entry_mode") or "").strip().upper()
    market_date = str(market_date or "").strip()
    content = str(final_content or "")
    path = str(decision_path or "").strip().lower()
    if not task_run_id or not occurrence_id or not requested_at or not market_date:
        raise ValueError("formal runtime completion requires runtime identity and market_date")
    if entry_mode not in {"MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"}:
        raise ValueError("formal runtime completion requires a registered entry mode")
    if path not in {"formal", "degraded", "timeout"}:
        raise ValueError("formal runtime completion requires formal, degraded, or timeout decision_path")
    validate_user_visible_content(content, canonical_closure_confirmed=False)
    validate_formal_decision_presentation_timestamp(content, requested_at)
    frozen_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    decision_id = f"ETF_FORMAL_DECISION:{task_run_id}"
    payload = {
        "request_id": task_run_id,
        "request_type": "SCHEDULED_REVIEW_COMPLETION",
        "formal_fact_type": "FORMAL_CURRENT_MAIN_DECISION",
        "source": "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER",
        "interaction_scenario": "FORMAL_DECISION",
        "task_id": "ETF正式决策",
        "task_run_id": task_run_id,
        "occurrence_id": occurrence_id,
        "entry_mode": entry_mode,
        "requested_at_beijing": requested_at,
        "market_date": market_date,
        "decision_path": path,
        "fact_source": "CURRENT_MAIN_LATEST_VALID_FIXED_PERIOD_FACTS",
        "fact_summary": fact_summary if isinstance(fact_summary, dict) else {},
        "decision_id": decision_id,
        "presentation_binding": {
            "report_type": "ETF_FORMAL_DECISION",
            "task_id": "ETF正式决策",
            "task_run_id": task_run_id,
            "occurrence_id": occurrence_id,
            "entry_mode": entry_mode,
            "request_id": task_run_id,
            "decision_id": decision_id,
            "report_id": decision_id,
            "full_content": content,
            "content_hash": frozen_hash,
            "content_hash_source": "completion_builder",
        },
    }
    return payload
