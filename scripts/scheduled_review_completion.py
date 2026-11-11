from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

try:
    from formal_entry_contract import formal_decision_title
    from runtime_session_gate import classify_live_snapshot_request
    from task_runtime_identity import derive_runtime_identity, derive_scheduled_review_identity
    from process_state_sync_request import (
        validate_current_lifecycle_contract,
        validate_managed_position_lifecycle,
    )
    from user_visible_presentation import validate_user_visible_content
    from validate_hyperliquid_consumer_output import validate_hyperliquid_consumer_output, validate_contribution_packet
except ModuleNotFoundError:
    from scripts.runtime_session_gate import classify_live_snapshot_request
    from scripts.task_runtime_identity import derive_runtime_identity, derive_scheduled_review_identity
    from scripts.process_state_sync_request import (
        validate_current_lifecycle_contract,
        validate_managed_position_lifecycle,
    )
    from scripts.user_visible_presentation import validate_user_visible_content
    from scripts.validate_hyperliquid_consumer_output import validate_hyperliquid_consumer_output, validate_contribution_packet

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


def validate_hyperliquid_consumer_if_present(review: dict, final_content: str) -> None:
    """Apply the shared Hyperliquid prose gate when a packet is carried."""
    review = review if isinstance(review, dict) else {}
    packet = review.get("hyperliquid_contribution") or review.get("hyperliquid")
    if not isinstance(packet, dict):
        return
    validate_contribution_packet(packet)
    validate_hyperliquid_consumer_output(str(final_content or ""), packet)


def validate_trade_review_presentation_semantics(
    review: dict, final_content: str, account: dict | None = None
) -> None:
    """Fail closed on direct contradictions between frozen prose and read objects.

    This is deliberately a narrow semantic guard, not a keyword-based content
    scorer.  It only blocks an explicit user-facing claim when the request-bound
    Discovery/opportunity projection proves the opposite.  A degraded or missing
    object therefore remains reportable; it cannot be converted into a false
    zero or a false "not available" claim.
    """
    content = str(final_content or "")
    discovery = review.get("formal_discovery") if isinstance(review, dict) else None
    opportunity = review.get("formal_opportunity") if isinstance(review, dict) else None
    if not isinstance(discovery, dict) and not isinstance(opportunity, dict):
        return

    # A correction/disclaimer is itself an acceptable semantic outcome.  For
    # example: “不能把资料不足写成没有机会”.
    correction = re.compile(r"(?:不能|不得|不可|不应|不能将|不得将).{0,20}(?:写成|断言|误读|等同|替代)")

    discovery_status = str((discovery or {}).get("status") or "").upper()
    discovery_count = len((discovery or {}).get("candidates") or [])
    discovery_ready = discovery_status in {"READY", "COMPLETED", "COMPLETE"} and discovery_count > 0
    discovery_missing_claim = re.compile(
        r"(?:没有|缺失|未形成|不可用|未完成).{0,16}(?:DISCOVERY|全市场发现)|"
        r"(?:DISCOVERY|全市场发现).{0,16}(?:没有|缺失|未形成|不可用|未完成)",
        re.IGNORECASE,
    )
    if discovery_ready and discovery_missing_claim.search(content) and not correction.search(content):
        raise ValueError(
            "trade review prose contradicts the request-bound Discovery object: "
            f"status={discovery_status}, candidate_count={discovery_count}"
        )

    opportunity_status = str((opportunity or {}).get("status") or "").upper()
    opportunity_count = (opportunity or {}).get("opportunity_count")
    opportunity_ready_count = (
        opportunity_count is not None
        and str(opportunity_status) not in {"", "UNKNOWN", "MISSING"}
    )
    zero_claim = re.compile(r"(?:机会(?:ETF)?(?:数量)?|机会层).{0,8}(?:为|是)?\s*0|没有机会", re.IGNORECASE)
    if opportunity_ready_count and opportunity_count not in (0, "0") and zero_claim.search(content) and not correction.search(content):
        raise ValueError(
            "trade review prose contradicts the request-bound opportunity object: "
            f"status={opportunity_status}, opportunity_count={opportunity_count}"
        )


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
    validate_trade_review_presentation_semantics(formal_review, final_content, account)
    validate_hyperliquid_consumer_if_present(formal_review, final_content)
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
    first_line = content.splitlines()[0].strip() if content.splitlines() else ""
    match = re.match(r"^【ETF正式决策】(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (正式判断|降级判断|超时判断)$", first_line)
    if not match:
        raise ValueError("formal decision presentation title has invalid timestamp or decision path")
    expected_prefix = formal_decision_title(requested, match.group(2))[:-4]
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
    # Formal Decision is a first-class Hyperliquid consumer. Apply the same
    # contribution-packet and prose-consumption gate as Trade Review so a
    # valid packet cannot be silently degraded into generic market language.
    validate_hyperliquid_consumer_if_present(formal_review, final_content)
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


def validate_system_review_user_facing_clarity(final_content: str) -> None:
    """Reject explicit internal identifiers in System Review's frozen user report only.

    Keep the guard narrow: ordinary ETF names, provider names, and technical facts
    needed to explain a real incident must remain reportable.
    """
    import re

    content = str(final_content or "")
    forbidden = (
        r"(?<![A-Za-z_])(?:request_id|task_run_id|occurrence_id|content_hash)\s*[:=]",
        r"CURRENT\.close_review_closure_is_stale",
        r"(?<![A-Za-z_])(?:CHATGPT_MANUAL_SYSTEM_REVIEW|CHATGPT_SCHEDULED_SYSTEM_REVIEW|MANUAL_CONVERSATION|AUTOMATIC_SCHEDULED_TASK)(?![A-Za-z_])",
    )
    if any(re.search(pattern, content, flags=re.IGNORECASE) for pattern in forbidden):
        raise ValueError("System Review user report exposes internal audit identifiers; revise before freezing")


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
    origin_mapping = {
        "CHATGPT_SCHEDULED_SYSTEM_REVIEW": "AUTOMATIC_SCHEDULED_TASK",
        "CHATGPT_SCHEDULED_SYSTEM_REVIEW_ACTOR": "AUTOMATIC_SCHEDULED_TASK",
        "CHATGPT_MANUAL_SCHEDULED_SYSTEM_REVIEW": "MANUAL_SCHEDULED_TASK",
        "CHATGPT_MANUAL_SYSTEM_REVIEW": "MANUAL_CONVERSATION",
    }
    # The live task contract permits platforms that do not expose nested
    # origin fields. Validate them fail-closed when present, but do not block
    # the completion path solely because the platform omitted them.
    if origin_source or origin_mode:
        if not origin_source or not origin_mode:
            raise ValueError("scheduled system review origin identity is incomplete")
        if origin_source not in origin_mapping or origin_mapping[origin_source] != origin_mode:
            raise ValueError("scheduled system review origin source/entry_mode mismatch")
        if origin_mode != entry_mode:
            raise ValueError("scheduled system review origin entry_mode does not match completion entry_mode")
    validate_system_review_presentation(system_review, final_content)
    validate_system_review_user_facing_clarity(final_content)
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
        "source_actor": str(completion.get("source") or "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER"),
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
        # Formal Decision uses the direct current-main completion contract.
        # Do not route it through the legacy formal-review/parent-request
        # builder: this task consumes already-published facts and has no
        # parent market-fact request.
        fact_binding = review.get("fact_binding") if isinstance(review.get("fact_binding"), dict) else {}
        supplied_summary = review.get("fact_summary") if isinstance(review.get("fact_summary"), dict) else {}
        fact_summary = dict(supplied_summary)
        if "fact_binding" not in fact_summary and fact_binding:
            fact_summary["fact_binding"] = fact_binding
        title = str(final_content or "").splitlines()[0].strip() if str(final_content or "").splitlines() else ""
        decision_path = {
            "正式判断": "formal",
            "降级判断": "degraded",
            "超时判断": "timeout",
        }.get(title.rsplit(" ", 1)[-1], "")
        if not decision_path:
            decision_path = str(review.get("decision_path") or "").strip().lower()
        identity = {
            "task_run_id": task_run_id or request_id,
            "occurrence_id": occurrence_id or task_run_id or request_id,
            "requested_at_beijing": requested_at_beijing,
            "entry_mode": entry_mode,
        }
        payload = build_formal_runtime_completion_request(
            run_identity=identity,
            market_date=market_date or str(review.get("market_date") or ""),
            final_content=final_content,
            decision_path=decision_path,
            fact_summary=fact_summary,
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



def select_latest_qualified_formal_discovery(
    discovery: dict, *, requested_at_beijing: str, market_date: str,
) -> dict | None:
    """Select the existing current-main Discovery only when legal at run start.

    No weekly fallback, new producer, new request, or market refresh.  A
    missing/invalid layer is None and must be disclosed as a limited layer.
    """
    from datetime import datetime
    if not isinstance(discovery, dict):
        return None
    if str(discovery.get("market_date") or "") != str(market_date):
        return None
    if str(discovery.get("status") or "").upper() not in {"READY", "COMPLETE"}:
        return None
    if str(discovery.get("coverage_status") or "").upper() not in {"COMPLETE", "READY"}:
        return None
    try:
        at = datetime.fromisoformat(str(discovery.get("generated_at_beijing") or ""))
        cutoff = datetime.fromisoformat(str(requested_at_beijing))
        if at.tzinfo is None or cutoff.tzinfo is None or at > cutoff:
            return None
    except (TypeError, ValueError):
        return None
    candidates = discovery.get("candidates")
    if not isinstance(candidates, list):
        return None
    codes = [str(c.get("code") or "").strip() for c in candidates if isinstance(c, dict)]
    if len(codes) != len(candidates) or any(not code for code in codes) or len(set(codes)) != len(codes):
        return None
    if discovery.get("candidate_count") != len(codes):
        return None
    if not str(discovery.get("latest_snapshot") or "").strip():
        return None
    return discovery


def normalize_legacy_formal_runtime_completion(payload: dict) -> dict:
    """Normalize the historical direct Formal Decision envelope in memory.

    Some scheduled-task writers emitted a complete FINAL_CONTENT with legacy
    labels (hashed task_id, human report_type, missing hash/report fields).
    Preserve the frozen body and runtime identity, but project the canonical
    notification identity so audit defects cannot suppress an otherwise unique
    original-body notification.
    """
    if not isinstance(payload, dict):
        return payload
    binding = payload.get("presentation_binding")
    content = str(payload.get("final_content") or "")
    source = str(payload.get("source") or "").strip().upper()
    entry_source = str(payload.get("formal_entry_source") or "").strip().upper()
    entry_mode = str(payload.get("entry_mode") or "").strip().upper()
    is_legacy_formal = (
        str(payload.get("interaction_scenario") or "").strip().upper() == "FORMAL_DECISION"
        and content.lstrip().startswith("【ETF正式决策】")
        and (
            source in {
                "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER",
                "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
                "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
                "CHATGPT_SCHEDULED_TASK",
                "CHATGPT_SCHEDULED_REVIEW_ACTOR",
            }
            or entry_source in {
                "CHATGPT_MANUAL_FORMAL_ANALYSIS",
                "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
                "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
                "CHATGPT_SCHEDULED_TASK",
            }
        )
        and entry_mode in {"MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK", "MANUAL_CONVERSATION"}
    )
    task_run_id = str(payload.get("task_run_id") or payload.get("request_id") or "").strip()
    occurrence_id = str(payload.get("occurrence_id") or task_run_id).strip()
    request_id = str(payload.get("request_id") or task_run_id).strip()
    if not task_run_id or not occurrence_id or not request_id or not content:
        return payload
    report_id = f"ETF_FORMAL_DECISION:{task_run_id}"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    payload["request_type"] = "SCHEDULED_REVIEW_COMPLETION"
    payload["formal_fact_type"] = "FORMAL_CURRENT_MAIN_DECISION"
    payload["task_id"] = "ETF正式决策"
    payload["task_run_id"] = task_run_id
    payload["occurrence_id"] = occurrence_id
    payload["request_id"] = request_id
    payload["report_type"] = "ETF_FORMAL_DECISION"
    payload["report_id"] = report_id
    payload["content_hash"] = content_hash
    # Historical direct completions may omit decision_path even though the
    # frozen title carries the exact path. Recover only that audit field.
    first_line = content.splitlines()[0].strip() if content.splitlines() else ""
    title_match = re.match(r"^【ETF正式决策】\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2} (正式判断|降级判断|超时判断)$", first_line)
    if title_match:
        payload["decision_path"] = title_match.group(1)
    binding = dict(binding) if isinstance(binding, dict) else {}
    binding.update({
        "report_type": "ETF_FORMAL_DECISION",
        "task_id": "ETF正式决策",
        "task_run_id": task_run_id,
        "occurrence_id": occurrence_id,
        "request_id": request_id,
        "report_id": report_id,
        "full_content": content,
        "content_hash": content_hash,
        "content_hash_source": "notification_compatibility_normalizer",
    })
    payload["presentation_binding"] = binding
    return payload


def validate_formal_runtime_completion_contract(payload: dict, *, root: Path | None = None) -> dict:
    """Fail closed before a Formal Decision completion can enter the notification chain.

    The completion must bind the frozen presentation to the exact current-main
    fact pointer consumed by the decision. This prevents an old snapshot from
    being delivered as a current run while preserving the existing immutable
    body/hash contract.
    """
    if not isinstance(payload, dict):
        raise ValueError("Formal Decision completion must be an object")
    payload = normalize_legacy_formal_runtime_completion(payload)
    binding = payload.get("presentation_binding")
    if not isinstance(binding, dict):
        raise ValueError("Formal Decision completion requires presentation_binding")
    if str(payload.get("task_id") or binding.get("task_id") or "").strip() != "ETF正式决策":
        raise ValueError("Formal Decision completion task identity mismatch")
    if str(payload.get("report_type") or binding.get("report_type") or "").strip().upper() != "ETF_FORMAL_DECISION":
        raise ValueError("Formal Decision completion report identity mismatch")
    if str(payload.get("interaction_scenario") or "").strip().upper() != "FORMAL_DECISION":
        raise ValueError("Formal Decision completion scenario mismatch")
    # Formal Decision completions can be written by either the direct/manual
    # GitHub contents path or the authorized ChatGPT scheduled-task runtime.
    # Both paths still pass the same identity, PIT, binding, hash and idempotency
    # checks below; this is source-contract compatibility, not a validation bypass.
    allowed_sources = {
        "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER",
        "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
        "CHATGPT_SCHEDULED_TASK",
        "CHATGPT_SCHEDULED_REVIEW_ACTOR",
        "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
    }
    # Some scheduled-task writers historically emitted a valid completion body
    # with null task_run_id/occurrence_id. Recover those two identity fields
    # deterministically from the already-required unique request_id, but only
    # for the authorized automatic scheduled Formal Decision source. This is
    # normalization of one completion, not a second run or a new event.
    source = str(payload.get("source") or "").strip().upper()
    entry_source = str(payload.get("formal_entry_source") or "").strip().upper()
    if (
        entry_source in {"CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "CHATGPT_SCHEDULED_TASK"}
        and str(payload.get("entry_mode") or "").strip().upper() == "AUTOMATIC_SCHEDULED_TASK"
    ):
        request_id = str(payload.get("request_id") or "").strip()
        if request_id and not str(payload.get("task_run_id") or "").strip():
            payload["task_run_id"] = "scheduled-formal:" + request_id
        if request_id and not str(payload.get("occurrence_id") or "").strip():
            payload["occurrence_id"] = "occurrence:" + request_id
    # Scheduled-task completions may use the top-level ``source`` for the\n    # fact provenance and carry the task writer identity separately in\n    # ``formal_entry_source``.  Accept that existing contract while keeping\n    # the writer identity mandatory and restricted to the same allow-list.\n    source = str(payload.get("source") or "").strip().upper()\n    entry_source = str(payload.get("formal_entry_source") or "").strip().upper()\n    if source not in allowed_sources and entry_source not in allowed_sources:\n        raise ValueError("Formal Decision completion source mismatch")
    if str(payload.get("formal_fact_type") or "").strip().upper() != "FORMAL_CURRENT_MAIN_DECISION":
        raise ValueError("Formal Decision completion fact type mismatch")
    if not str(payload.get("report_id") or binding.get("report_id") or "").strip():
        raise ValueError("Formal Decision completion missing report_id")
    for key in ("task_run_id", "occurrence_id", "request_id", "requested_at_beijing", "market_date", "decision_path"):
        if not str(payload.get(key) or "").strip():
            raise ValueError("Formal Decision completion missing " + key)
    for key in ("task_run_id", "occurrence_id", "request_id"):
        if any(part in str(payload.get(key) or "") for part in ("/", "\\", "..")):
            raise ValueError("Formal Decision completion identity is not path-safe")
    content = str(payload.get("final_content") or "")
    bound_content = str(binding.get("full_content") or binding.get("final_content") or "")
    if not content or not bound_content or bound_content != content:
        raise ValueError("Formal Decision completion frozen content is missing or mismatched")
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # The scheduled-task runtime may persist the same frozen completion before
    # the canonical owner has had a chance to materialize derived hash fields.
    # Normalize only missing hashes in-memory; any existing hash drift remains a
    # hard failure. This preserves one completion/body while allowing the
    # existing notification projection to consume the same run.
    top_hash = str(payload.get("content_hash") or "").strip().lower()
    if top_hash and top_hash != expected_hash:
        raise ValueError("Formal Decision completion top-level content_hash mismatch")
    binding_hash = str(binding.get("content_hash") or "").strip().lower()
    if binding_hash and binding_hash != expected_hash:
        raise ValueError("Formal Decision completion presentation_binding.content_hash mismatch")
    payload["content_hash"] = expected_hash
    binding["content_hash"] = expected_hash
    if "final_content_hash" in payload:
        final_hash = str(payload.get("final_content_hash") or "").strip().lower()
        if final_hash and final_hash != expected_hash:
            raise ValueError("Formal Decision completion final_content_hash mismatch")
        payload["final_content_hash"] = expected_hash
    # New builders mark the current presentation contract explicitly.
    # Historical frozen completions remain replayable when identity, binding,
    # snapshot and hash checks pass; they must not be rejected solely because
    # the display contract was strengthened after the original run.
    if str(payload.get("completion_contract_version") or "").strip() == "2":
        validate_user_visible_content(content, canonical_closure_confirmed=False)
    validate_formal_decision_presentation_timestamp(content, str(payload["requested_at_beijing"]))
    title = content.splitlines()[0].strip() if content.splitlines() else ""
    expected_label = {
        "formal": "正式判断",
        "degraded": "降级判断",
        "timeout": "超时判断",
    }.get(str(payload["decision_path"]).strip().lower())
    if expected_label and not title.endswith(expected_label):
        raise ValueError("Formal Decision completion decision_path does not match title")
    fact_binding = payload.get("fact_binding")
    if not isinstance(fact_binding, dict):
        raise ValueError("Formal Decision completion requires fact_binding")
    base = (root or ROOT).resolve()
    # Validate the snapshot pinned by the run, not whatever CURRENT points to
    # later. CURRENT may legitimately advance between decision and handoff.
    snapshot_ref = str(fact_binding.get("latest_snapshot") or "").strip().replace("\\", "/")
    if not snapshot_ref:
        raise ValueError("Formal Decision completion fact_binding missing latest_snapshot")
    snapshot_path = (base / snapshot_ref).resolve()
    if base not in snapshot_path.parents or not snapshot_path.exists():
        raise ValueError("Formal Decision completion bound snapshot pointer is invalid")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    expected_binding = {
        "market_date": str(fact_binding.get("market_date") or "").strip(),
        "latest_valid_node": str(fact_binding.get("latest_valid_node") or "").strip(),
        "latest_snapshot": snapshot_ref,
        "captured_at_beijing": str(fact_binding.get("captured_at_beijing") or "").strip(),
        "fact_status": str(fact_binding.get("fact_status") or "").strip(),
    }
    for key, expected in expected_binding.items():
        if not expected:
            raise ValueError("Formal Decision completion fact_binding missing " + key)
        if str(fact_binding.get(key) or "").strip() != expected:
            raise ValueError("Formal Decision completion fact_binding mismatch: " + key)
    if str(snapshot.get("market_date") or "").strip() != expected_binding["market_date"]:
        raise ValueError("Formal Decision completion bound snapshot market_date mismatch")
    if str(snapshot.get("quality_status") or "").strip().upper() != "PASS":
        raise ValueError("Formal Decision completion bound snapshot quality is not PASS")
    return {
        "task_run_id": str(payload["task_run_id"]).strip(),
        "occurrence_id": str(payload["occurrence_id"]).strip(),
        "request_id": str(payload["request_id"]).strip(),
        "decision_path": str(payload["decision_path"]).strip().lower(),
        "fact_binding": dict(fact_binding),
    }


def build_formal_evidence_consumption_audit(fact_summary: dict | None, final_content: str = "") -> dict:
    """Build the request-bound, object-level Hyperliquid consumption ledger.

    The ledger is richer than a direct ETF-code join. Economic-family mappings,
    observed fields, and cross-engine relations remain available to Formal
    Decision without turning missing direct codes into global evidence absence.
    """
    facts = fact_summary if isinstance(fact_summary, dict) else {}
    discovery = facts.get("formal_discovery") or facts.get("discovery") or {}
    pack = facts.get("decision_fact_pack") or {}
    hyper = facts.get("hyperliquid_external_driver_context") or facts.get("hyperliquid") or {}
    bindings = [item for item in (hyper.get("object_bindings") if isinstance(hyper, dict) else []) if isinstance(item, dict)]
    binding_by_code: dict[str, list[dict]] = {}
    for item in bindings:
        code = str(item.get("code") or item.get("object_code") or "").strip()
        if code:
            binding_by_code.setdefault(code, []).append(item)
    observations = [item for item in (hyper.get("observations") or []) if isinstance(item, dict)]
    observation_by_asset = {
        str(item.get("asset") or item.get("name") or "").strip(): item
        for item in observations
        if str(item.get("asset") or item.get("name") or "").strip()
    }
    objects: dict[str, dict] = {}

    def add(items, layer):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or item.get("symbol") or item.get("object_code") or "").strip()
            if not code:
                continue
            obj = objects.setdefault(code, {
                "object_code": code,
                "object_name": str(item.get("name") or item.get("object_name") or code),
                "object_layers": [],
                "source_refs": [],
            })
            if layer not in obj["object_layers"]:
                obj["object_layers"].append(layer)
            for key in ("source_path", "identity", "discovery_identity", "fact_ref"):
                value = str(item.get(key) or "").strip()
                if value and value not in obj["source_refs"]:
                    obj["source_refs"].append(value)

    add(discovery.get("candidates") if isinstance(discovery, dict) else [], "DISCOVERY")
    add(pack.get("discovery_inputs"), "DISCOVERY")
    add(pack.get("observation_inputs"), "OBSERVATION")
    add(pack.get("observation_review_inputs"), "OBSERVATION")
    add(pack.get("positions"), "HOLDING")
    add(pack.get("opportunity_inputs"), "OPPORTUNITY")

    def classify_effect(matched: list[dict]) -> str:
        explicit = [str(item.get("decision_effect") or "").strip().upper() for item in matched]
        for value in explicit:
            if value in {"SUPPORT", "CONFIRMATORY", "CONTRADICTION", "TIMING_FILTER", "CROWDING_RISK", "NO_INCREMENTAL_VALUE", "NO_DIRECT_MAPPING", "INSUFFICIENT"}:
                return value
        relations = {
            str((item.get("cross_engine_validation") or {}).get("relation") or "").upper()
            for item in matched
            if isinstance(item.get("cross_engine_validation"), dict)
        }
        if "CROSS_MARKET_DIVERGENCE" in relations:
            return "CONTRADICTION"
        if "EXTERNAL_CONFIRMED_A_SHARE" in relations:
            return "CONFIRMATORY"
        if "EXTERNAL_NOT_TRANSMITTED" in relations:
            return "TIMING_FILTER"
        return "INSUFFICIENT" if matched else "NO_DIRECT_MAPPING"

    object_audit = []
    for code, obj in objects.items():
        matched = binding_by_code.get(code) or []
        assets = sorted({
            str(asset).strip()
            for item in matched
            for asset in (item.get("external_assets") or [])
            if str(asset).strip()
        })
        raw_evidence_status = "READY" if any(int(item.get("observation_count") or 0) > 0 for item in matched) else "DEGRADED"
        if str(hyper.get("quality_status") or "").upper() in {"MISSING", "NOT_REQUESTED", "UNAVAILABLE", "FAILED"}:
            raw_evidence_status = "FAILED"
        effect = classify_effect(matched)
        matched_observations = [observation_by_asset[asset] for asset in assets if asset in observation_by_asset]
        signal_fields = [{
            "asset": item.get("asset") or item.get("name") or "",
            "family": item.get("family") or "",
            "direction": item.get("direction") or "",
            "funding": item.get("funding") or item.get("funding_rate") or "",
            "open_interest": item.get("open_interest") or item.get("openInterest") or "",
            "premium": item.get("premium") or "",
            "crowding": item.get("crowding") or item.get("crowding_status") or "",
            "observation_as_of": item.get("observation_as_of") or "",
        } for item in matched_observations]
        relations = sorted({
            str((item.get("cross_engine_validation") or {}).get("relation") or "").strip()
            for item in matched
            if str((item.get("cross_engine_validation") or {}).get("relation") or "").strip()
        })
        observation_as_of = str(next((item.get("observation_as_of") for item in matched if str(item.get("observation_as_of") or "").strip()), "") or hyper.get("as_of_beijing") or hyper.get("latest_observation_collected_at_beijing") or "")
        object_audit.append({
            **obj,
            "object_type": "A_SHARE_ETF_OBJECT",
            "mapped_assets": assets,
            "mapping_reason": "CONFIGURED_DIRECT_OR_ECONOMIC_FAMILY_BINDING" if matched else "NO_CURRENT_OBJECT_BINDING",
            "observation_as_of": observation_as_of,
            "evidence_status": raw_evidence_status,
            "consumption_status": "CONSUMED" if matched and raw_evidence_status == "READY" else "CONSUMED_AS_LIMITATION",
            "decision_effect": effect,
            "evidence_summary": {
                "mapped_assets": assets,
                "observation_count": sum(int(item.get("observation_count") or 0) for item in matched),
                "hyperliquid_status": str(hyper.get("quality_status") or ""),
                "signal_fields": signal_fields,
                "cross_engine_relations": relations,
                "time_relation": str(next((item.get("time_relation") for item in matched if str(item.get("time_relation") or "").strip()), "")),
            },
            "decision_use": str(next((item.get("decision_use") for item in matched if str(item.get("decision_use") or "").strip()), "") or (
                "已映射证据用于比较外部方向与A股对象的同步/背离，并过滤入场时机或拥挤风险；不独立授予交易权限"
                if matched else "当前没有该对象的可确认Hyperliquid绑定；不得把其他对象或全局脉冲替代本对象判断"
            )),
            "business_impact": effect,
        })

    audit_status = "PASS_EMPTY" if not object_audit else ("PASS" if all(
        item.get("decision_effect")
        and item.get("evidence_status") in {"READY", "DEGRADED", "FAILED", "NOT_APPLICABLE"}
        and item.get("consumption_status") in {"CONSUMED", "CONSUMED_AS_LIMITATION", "NOT_CONSUMED"}
        and "decision_use" in item and "evidence_summary" in item
        for item in object_audit
    ) else "PARTIAL")
    return {
        "schema_version": "2.1",
        "audit_status": audit_status,
        "discovery": {
            "status": "PROVIDED" if discovery else "NOT_PROVIDED",
            "identity": str(discovery.get("identity") or "").strip() if isinstance(discovery, dict) else "",
            "generated_at_beijing": discovery.get("generated_at_beijing", "") if isinstance(discovery, dict) else "",
            "latest_snapshot": discovery.get("latest_snapshot", "") if isinstance(discovery, dict) else "",
            "candidate_count": discovery.get("candidate_count") if isinstance(discovery, dict) else None,
            "coverage_status": discovery.get("coverage_status", "") if isinstance(discovery, dict) else "",
        },
        "hyperliquid": {
            "status": "PROVIDED" if hyper else "NOT_PROVIDED",
            "context_id": hyper.get("context_id", "") if isinstance(hyper, dict) else "",
            "quality_status": hyper.get("quality_status", "") if isinstance(hyper, dict) else "",
            "observation_count": hyper.get("observation_count") if isinstance(hyper, dict) else None,
            "object_binding_count": len(bindings),
            "time_basis": hyper.get("time_basis", "") if isinstance(hyper, dict) else "",
            "phase": hyper.get("consumption_phase") or hyper.get("market_phase") or "",
        },
        "object_audit": object_audit,
        "enforcement": "AUDIT_ONLY; incomplete evidence is reported locally and never converted into a trade block",
    }

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
    evidence_audit = build_formal_evidence_consumption_audit(fact_summary, content)
    if isinstance(fact_summary, dict) and fact_summary and evidence_audit.get("audit_status") not in {"PASS", "PASS_EMPTY"}:
        raise ValueError("formal decision evidence consumption audit is incomplete; object-level ledger is required")
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
        "evidence_consumption_audit": evidence_audit,
        "fact_binding": (
            fact_summary.get("fact_binding")
            if isinstance(fact_summary, dict) and isinstance(fact_summary.get("fact_binding"), dict)
            else {}
        ),
        "completion_contract_version": "2",
        "decision_id": decision_id,
        "report_type": "ETF_FORMAL_DECISION",
        "report_id": decision_id,
        "final_content": content,
        "content_hash": frozen_hash,
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
