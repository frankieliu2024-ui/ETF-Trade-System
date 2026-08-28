from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
APAC_LATE_HK_MIN_DELTA_PCT = 0.8


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _market_value_error(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    category = str(ctx.get("event_category") or ctx.get("shock_severity") or "")
    if category not in {"EXTREME", "SUDDEN", "REVERSAL", "DIVERGENCE"}:
        return "market change notification has no recognized price/path event category"
    if not str(ctx.get("market_as_of_beijing") or ""):
        return "market change notification has no auditable market timestamp"

    day = _number(ctx.get("day_change_pct"))
    phase = _number(ctx.get("phase_metric_change_pct"))
    magnitude = _number(ctx.get("event_magnitude_pct"))
    sudden = _number(ctx.get("sudden_change_pct"))
    interval = _number(ctx.get("interval_minutes"))

    if category == "EXTREME" and day is None and phase is None and magnitude is None:
        return "EXTREME notification has no numeric price evidence"
    if category == "SUDDEN" and sudden is None and phase is None:
        return "SUDDEN notification has no comparable pulse move"
    if category == "SUDDEN" and interval is not None and interval <= 0:
        return "SUDDEN notification has invalid comparison interval"
    if category == "REVERSAL" and magnitude is None:
        return "REVERSAL notification has no excursion magnitude"
    if category == "REVERSAL" and day is None and phase is None:
        return "REVERSAL notification has no current comparable level"
    if category == "DIVERGENCE" and magnitude is None:
        return "DIVERGENCE notification has no numeric spread magnitude"
    return ""


def _late_apac_error(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    if str(ctx.get("session_node") or "") != "HK_LATE_UPDATE":
        return ""
    if "主总结兜底" in str(event.get("title") or ""):
        return ""
    delta = _number(ctx.get("hstech_change_since_primary_pct"))
    if delta is None:
        return "HK late update has no comparable HSTECH delta; coverage/tone drift is not market change"
    if abs(delta) < APAC_LATE_HK_MIN_DELTA_PCT:
        return f"HK late update HSTECH delta {delta:+.2f}% is below materiality threshold"
    return ""


def _formal_decision_error(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    current_status = str(ctx.get("opportunity_status") or "")
    previous_status = str(ctx.get("previous_opportunity_status") or "")
    current_code = str(ctx.get("security_code") or event.get("security_code") or "")
    previous_code = str(ctx.get("previous_security_code") or "")
    current_risk = str(ctx.get("risk_permission") or "")
    previous_risk = str(ctx.get("previous_risk_permission") or "")
    holding_changed = bool(ctx.get("holding_action_changed"))

    opportunity_changed = bool(current_status and previous_status and current_status != previous_status)
    candidate_changed = bool(current_code and previous_code and current_code != previous_code)
    risk_changed = bool(current_risk and previous_risk and current_risk != previous_risk)
    if not (opportunity_changed or candidate_changed or risk_changed or holding_changed):
        return "formal decision change has no complete before/after decision evidence"
    if not str(ctx.get("decision_id") or event.get("related_decision_id") or ""):
        return "formal decision change has no decision id"
    return ""


def _account_error(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    ids = [str(x) for x in (ctx.get("account_event_ids") or []) if str(x)]
    if not ids:
        return "account change notification has no reconciliable account event ids"
    if not str(ctx.get("account_event_time_beijing") or ""):
        return "account change notification has no account fact timestamp"
    return ""


def _execution_error(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    if not str(ctx.get("decision_id") or event.get("related_decision_id") or ""):
        return "execution confirmation has no linked decision"
    if not str(ctx.get("security_code") or event.get("security_code") or ""):
        return "execution confirmation has no security code"
    if str(ctx.get("side") or "") not in {"BUY", "SELL"}:
        return "execution confirmation has no valid execution side"
    if not str(ctx.get("lifecycle") or ""):
        return "execution confirmation has no lifecycle context"
    return ""


def _decision_trigger_error(event: dict) -> str:
    trigger = _read_json(STATE / "decision_trigger.json")
    if str(trigger.get("status") or "") not in {"TRIGGERED", "ALREADY_RECORDED"}:
        return "decision trigger is not currently active"
    if not bool(trigger.get("requires_formal_reassessment")):
        return "decision trigger does not require reassessment"
    if not str(trigger.get("idempotency_key") or ""):
        return "decision trigger has no idempotency key"
    trigger_type = str(trigger.get("trigger_type") or "")
    if trigger_type == "ACCOUNT_STRUCTURE_CHANGED":
        return "account structure changes must use the dedicated account-fact path"
    if trigger_type == "TRADE_CONFIRMED":
        if not str(trigger.get("applicable_object") or ""):
            return "confirmed-trade trigger has no trade event id"
        return ""
    if trigger_type in {"RISK_BOUNDARY_CROSSED", "E2E_RECOVERED"}:
        if not str(trigger.get("evidence_change") or ""):
            return f"{trigger_type} has no explicit before/after evidence"
        return ""
    if not str(trigger.get("evidence_change") or ""):
        return "generic reassessment trigger has no explicit evidence change"
    return ""


def _system_error(event: dict) -> str:
    source = str(event.get("source") or "")
    if source == "self_healing_status":
        state = _read_json(STATE / "self_healing_status.json")
        classification = str(state.get("classification") or "")
        action = str(state.get("recommended_action") or "")
        if action != "ESCALATE" and classification not in {"PERSISTENT_RUNTIME_FAILURE", "CONSISTENCY_REGRESSION"}:
            return "self-healing notification has no current escalation evidence"
        if not str(state.get("checked_at") or state.get("updated_at") or ""):
            return "self-healing notification has no diagnostic timestamp"
        return ""
    if source == "workflow_failure_diagnostic":
        diag = _read_json(STATE / "workflow_failure_diagnostic.json")
        safety = diag.get("safety") or {}
        if str(diag.get("recommended_action") or "") != "ESCALATE_WITH_DIAGNOSTIC":
            return "workflow failure notification has no escalation recommendation"
        if not bool(safety.get("head_is_current_main")):
            return "workflow failure diagnostic is not for current main"
        if not str(diag.get("run_id") or ""):
            return "workflow failure diagnostic has no run id"
        return ""
    return "system notification has no recognized diagnostic source"


def notification_evidence_error(event: dict | None) -> str:
    """Return a rejection reason when a user-visible change lacks comparable facts.

    Missing data, coverage-set changes, provider switches, freshness/quality labels,
    or derived wording changes are never sufficient evidence of a market/trading
    state change. Fixed summaries/reminders/tests are not change events and are
    intentionally outside this contract.
    """
    if not event:
        return ""
    event_type = str(event.get("event_type") or event.get("type") or "")
    source = str(event.get("source") or "")

    if event_type in {"MARKET_VALUE_ALERT", "MARKET_SHOCK_ALERT"}:
        return _market_value_error(event)
    if event_type == "APAC_SESSION_SUMMARY":
        return _late_apac_error(event)
    if event_type == "FORMAL_DECISION_MATERIAL_CHANGE":
        return _formal_decision_error(event)
    if event_type == "ACCOUNT_FACT_CONFIRMATION":
        return _account_error(event)
    if event_type == "PENDING_EXECUTION_CONFIRMATION":
        return _execution_error(event)
    if source == "decision_trigger" or event_type == "交易判断":
        return _decision_trigger_error(event)
    if source in {"self_healing_status", "workflow_failure_diagnostic"} or event_type in {"系统异常", "SYSTEM_RUNTIME_BLOCKER"}:
        return _system_error(event)
    return ""
