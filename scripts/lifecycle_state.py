"""Canonical, read-only projection of executed Trial lifecycles.

Lifecycle state is derived from formal decision and execution facts.  It is
not a second event store and it never creates a permission, order, or trade.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _load(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)


def _is_trading_day(day: date, calendar: dict[str, Any]) -> bool:
    return day.weekday() < 5 and day.isoformat() not in set(calendar.get("closed_dates") or [])


def add_trading_days(start: date, count: int, calendar: dict[str, Any]) -> date:
    """Return the count-th A-share trading day after start (T+0 is start)."""
    if count <= 0:
        return start
    day, remaining = start, count
    while remaining:
        day += timedelta(days=1)
        if _is_trading_day(day, calendar):
            remaining -= 1
    return day


def trading_day_offset(start: date, end: date, calendar: dict[str, Any]) -> int | None:
    if end < start or not _is_trading_day(start, calendar):
        return None
    offset, day = 0, start
    while day < end:
        day += timedelta(days=1)
        if _is_trading_day(day, calendar):
            offset += 1
    return offset


def _formal_decisions(root: Path) -> list[dict[str, Any]]:
    result = []
    directories = [root / "events" / "decisions", root / "events" / "reviews"]
    paths = []
    for directory in directories:
        if directory.exists():
            paths.extend(sorted(directory.glob("*.json")))
    for path in paths:
        event = _load(path, {})
        formal = event.get("formal_decision") or {}
        if not isinstance(formal, dict):
            formal = {}
        event_type = str(event.get("event_type") or "").upper()
        if event_type == "FORMAL_DECISION_REPORT" and not formal:
            binding = event.get("presentation_binding") or {}
            content = str(binding.get("full_content") or event.get("final_content") or "")
            code_match = re.search(r"(?:候选|机会ETF)[^\n]*?(\d{6})", content)
            trial_match = re.search(r"(\d{6})[^\n。；]*?(?:进入Trial|Trial评估|Trial上限)", content)
            code = (trial_match or code_match).group(1) if (trial_match or code_match) else ""
            if code and re.search(rf"{code}[^\n。；]*?(?:进入Trial|Trial评估|Trial上限)", content):
                event = dict(event)
                event["formal_decision"] = {
                    "lifecycle": "允许Trial",
                    "candidate_code": code,
                    "candidate_name": "",
                    "main_candidate": content,
                }
                formal = event["formal_decision"]
        lifecycle = str(formal.get("lifecycle") or event.get("lifecycle") or "")
        risk_permission = str(formal.get("risk_permission") or event.get("risk_permission") or "")
        # Some canonical Formal Decision events recorded the Trial grant in
        # risk_permission while leaving lifecycle/hypothesis_id unresolved.
        # The linked executed BUY remains the required second fact; this
        # recovery must never infer a lifecycle from holdings or a symbol alone.
        trial_granted = "Trial" in lifecycle or "允许Trial" in risk_permission
        if not trial_granted:
            continue
        # A few historical events carry a permissive risk_permission while the
        # candidate-specific decision is only observation/exit.  Do not let
        # that stale field manufacture a Trial, especially when the same
        # hypothesis belongs to a later, genuinely executed BUY.
        candidate_code = str(event.get("candidate_code") or formal.get("candidate_code") or "").strip()
        candidate_name = str(event.get("candidate_name") or formal.get("candidate_name") or "").strip()
        candidate_text = " ".join(
            str(formal.get(key) or event.get(key) or "")
            for key in ("lifecycle", "main_candidate", "amount_action", "decisive_reason")
        )
        identity_present = bool(
            (candidate_code and candidate_code in candidate_text)
            or (candidate_name and candidate_name in candidate_text)
        )
        main_candidate_text = str(formal.get("main_candidate") or event.get("main_candidate") or "")
        amount_text = str(formal.get("amount_action") or event.get("amount_action") or "")
        candidate_trial = any(token in main_candidate_text for token in (
            "Trial机会成立", "Trial评估成立", "进入Trial并", "试探性部署成立",
            "已执行Trial", "Trial已执行", "Trial评估",
        ))
        candidate_observation_only = (
            any(token in main_candidate_text for token in ("观察", "观察机会"))
            and not candidate_trial
            and candidate_code not in amount_text
            and candidate_name not in amount_text
        )
        if identity_present and candidate_observation_only:
            continue
        if not event.get("hypothesis_id") and not ("允许Trial" in risk_permission and event.get("candidate_code")) and not (event_type == "FORMAL_DECISION_REPORT" and candidate_trial and candidate_code):
            continue
        result.append(event)
    return result


def _completion_trial_decisions(root: Path, executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover executed Trial identity from the linked canonical completion.

    No hypothesis ID is fabricated: the completion identity remains the
    attribution key, and this projection never grants trading authority.
    """
    result = []
    for execution in executions:
        if str(execution.get("side") or "").upper() != "BUY":
            continue
        linked = str(execution.get("linked_decision_id") or "")
        prefix = "FORMAL_COMPLETION:"
        if not linked.startswith(prefix):
            continue
        request_id = linked[len(prefix):]
        if not request_id or "/" in request_id or "\\\\" in request_id:
            continue
        completion = _load(
            root / "requests" / "live_snapshot" / (request_id + "__scheduled_review_completion.json"), {}
        )
        if completion.get("request_id") != request_id or completion.get("report_type") != "ETF_FORMAL_DECISION":
            continue
        binding = completion.get("presentation_binding") or {}
        content = str(completion.get("final_content") or binding.get("full_content") or "")
        code = str(execution.get("code") or "")
        if "【机会ETF逐只评估】" not in content:
            continue
        opportunity_section = content.split("【机会ETF逐只评估】", 1)[1].split("【", 1)[0]
        if code not in opportunity_section or not any(
            phrase in opportunity_section for phrase in ("Trial评估成立", "达到Trial评估标准")
        ):
            continue
        result.append({
            "decision_id": linked,
            "candidate_code": code,
            "candidate_name": execution.get("name"),
            "hypothesis_id": None,
            "decision_time_beijing": completion.get("requested_at_beijing"),
            "_linked_execution_id": execution.get("event_id"),
            "_completion_trial": True,
        })
    # Recovery path for confirmed executions whose ingress retained an old
    # or missing decision attribution.  The completion is the canonical frozen
    # Formal Decision record; it may identify the same security and Trial
    # judgement even when the broker sync carried a stale linked_decision_id.
    # This is projection-only: it never mutates the trade fact or creates a
    # permission.  Ambiguous/multiple same-window Trial judgements fail closed.
    completions = []
    completion_dir = root / "requests" / "live_snapshot"
    for path in sorted(completion_dir.glob("*__scheduled_review_completion.json")) if completion_dir.exists() else []:
        completion = _load(path, {})
        if str(completion.get("task_name") or completion.get("task_id") or "").strip() != "ETF正式决策" or (completion.get("report_type") and completion.get("report_type") != "ETF_FORMAL_DECISION"):
            continue
        decision_id = str(completion.get("decision_id") or "").strip()
        decision_time = parse_time(completion.get("requested_at_beijing"))
        binding = completion.get("presentation_binding") or {}
        content = str(completion.get("final_content") or binding.get("full_content") or "")
        if not decision_id or not decision_time or not content:
            continue
        for execution in executions:
            if str(execution.get("side") or "").upper() != "BUY":
                continue
            code = str(execution.get("code") or "").strip()
            if not code or decision_time > (parse_time(execution.get("confirmed_at_beijing") or execution.get("executed_at_beijing")) or datetime.max.replace(tzinfo=SHANGHAI)):
                continue
            if code not in content:
                continue
            # Candidate-level Trial wording is required; generic risk or
            # portfolio wording must not manufacture a lifecycle.  Support the
            # live decision wording “建议人工确认…Trial” while keeping the
            # security-code match and completion identity mandatory.
            opportunity_text = content
            if "【机会ETF逐只评估】" in content:
                opportunity_text = content.split("【机会ETF逐只评估】", 1)[1].split("【", 1)[0]
            if code not in opportunity_text or not any(
                phrase in opportunity_text
                for phrase in (
                    "Trial通过审查",
                    "进入Trial审查",
                    "Trial评估成立",
                    "达到Trial评估标准",
                    "建议人工确认",
                    "优先Trial",
                )
            ):
                continue
            existing = [x for x in completions if x["decision_id"] == decision_id and x["candidate_code"] == code]
            if existing:
                continue
            completions.append({
                "decision_id": decision_id,
                "candidate_code": code,
                "candidate_name": execution.get("name"),
                "hypothesis_id": None,
                "decision_time_beijing": completion.get("requested_at_beijing"),
                "_linked_execution_id": execution.get("event_id"),
                "_completion_trial": True,
                "_attribution_recovery": "FROZEN_FORMAL_COMPLETION_SECURITY_MATCH",
            })
    result.extend(completions)
    return result


def _state_sync_trial_decisions(root: Path, executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover an explicitly recorded Trial from the canonical state-sync ingress.

    This is a read-only recovery for broker-confirmed executions whose formal
    decision record predates the state-sync ingress or is no longer resolvable.
    It accepts only an exact ``Trial`` marker on an executed broker-screenshot
    trade with a non-empty decision attribution.  It never infers a lifecycle
    from holdings, a symbol, or a current Dashboard row, and it never grants
    trading authority.
    """
    result = []
    for execution in executions:
        if str(execution.get("side") or "").upper() != "BUY":
            continue
        if str(execution.get("lifecycle") or "").strip() != "Trial":
            continue
        if str(execution.get("source") or "").strip() != "CHATGPT_USER_BROKER_SCREENSHOT":
            continue
        decision_id = str(execution.get("linked_decision_id") or "").strip()
        code = str(execution.get("code") or "").strip()
        event_id = str(execution.get("event_id") or "").strip()
        if not decision_id or not code or not event_id:
            continue
        result.append({
            "decision_id": decision_id,
            "candidate_code": code,
            "candidate_name": execution.get("name"),
            "hypothesis_id": execution.get("hypothesis_id"),
            "decision_time_beijing": execution.get("confirmed_at_beijing") or execution.get("executed_at_beijing"),
            "_linked_execution_id": event_id,
            "_state_sync_trial": True,
        })
    return result


def _executions(root: Path) -> list[dict[str, Any]]:
    result = []
    directory = root / "events" / "trades"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        event = _load(path, {})
        if str(event.get("execution_status") or "").upper() == "EXECUTED":
            # Attribution corrections are projection inputs, not mutations of
            # the immutable broker trade fact.  Lifecycle must consume the
            # same canonical attribution resolver as execution-quality and
            # consistency checks; otherwise a repaired trade remains invisible
            # to the Trial projection.
            projected = dict(event)
            try:
                from decision_trade_link import resolve_link
            except ModuleNotFoundError:
                from scripts.decision_trade_link import resolve_link
            resolved, _decision, _status = resolve_link(root, projected, str(projected.get("linked_decision_id") or ""))
            if resolved:
                projected["linked_decision_id"] = resolved
            result.append(projected)
    return result


def _resolution(root: Path, trial: dict[str, Any], as_of: date) -> dict[str, Any] | None:
    """Resolve a Trial from a later decision or uniquely attributable exit.

    Historical/overdue Trials may be covered by the latest formal decision for
    the same security even when the old event lacks a hypothesis link.  This
    is a decision-coverage projection only; it never attributes a trade or
    closes a specific lot.
    """
    hypothesis = str(trial.get("hypothesis_id") or "")
    code = str(trial.get("candidate_code") or trial.get("candidate_code") or "").strip()
    decision_time = parse_time(trial.get("decision_time_beijing"))
    candidates = []
    directory = root / "events" / "decisions"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        event = _load(path, {})
        if (not hypothesis or event.get("decision_id") == trial.get("decision_id")
                or str(event.get("hypothesis_id") or "") != hypothesis):
            continue
        event_time = parse_time(event.get("decision_time_beijing"))
        if decision_time and (event_time or decision_time) < decision_time:
            continue
        formal = event.get("formal_decision") or {}
        lifecycle = str(formal.get("lifecycle") or "")
        if event.get("hypothesis_closed") or str(formal.get("hypothesis_status") or "").upper() == "CLOSED":
            status = "FAILED_EXIT_EVALUATION" if ("退出" in lifecycle or "失败" in lifecycle) else "RESOLVED"
        elif "Confirm" in lifecycle:
            status = "CONFIRM_EVALUATION"
        else:
            continue
        candidates.append((event_time or datetime.min.replace(tzinfo=SHANGHAI), status, event))
    if candidates:
        _, status, event = max(candidates, key=lambda x: x[0])
        return {"status": status, "decision_id": event.get("decision_id"), "decision_time_beijing": event.get("decision_time_beijing", "")}

    # Legacy/overdue Trial fallback: consume the latest explicit business
    # judgment for this security after the mandatory node.  A mere holding
    # list or code occurrence is insufficient; the decision text must contain
    # the security identity and an action-bearing lifecycle statement.
    mandatory = trial.get("mandatory_decision_market_date")
    if mandatory:
        try:
            mandatory_date = date.fromisoformat(str(mandatory))
        except ValueError:
            mandatory_date = None
    else:
        mandatory_date = None
    if mandatory_date and as_of >= mandatory_date:
        explicit = []
        name = str(trial.get("candidate_name") or "").strip()
        for path in sorted(directory.glob("*.json")) if directory.exists() else []:
            event = _load(path, {})
            event_time = parse_time(event.get("decision_time_beijing"))
            if not event_time or event_time.date() < mandatory_date:
                continue
            if event.get("decision_id") == trial.get("decision_id"):
                continue
            formal = event.get("formal_decision") or {}
            text = " ".join(str(event.get(key) or "") for key in (
                "candidate_code", "candidate_name", "lifecycle", "main_candidate",
                "amount_action", "decisive_reason",
            )) + " " + " ".join(str(formal.get(key) or "") for key in (
                "candidate_code", "candidate_name", "lifecycle", "main_candidate",
                "amount_action", "decisive_reason",
            ))
            if code not in text and (not name or name not in text):
                continue
            if not any(token in text for token in ("持有", "追加", "Trial", "Confirm", "退出", "减持", "卖出", "观察")):
                continue
            if "Confirm" in text or "确认部署" in text:
                status = "CONFIRM_EVALUATION"
            elif any(token in text for token in ("退出", "减持", "卖出", "失败")):
                status = "FAILED_EXIT_EVALUATION"
            else:
                status = "HOLDING_MANAGEMENT"
            explicit.append((event_time, status, event))
        if explicit:
            _, status, event = max(explicit, key=lambda x: x[0])
            return {
                "status": status,
                "resolution_reason": "LATEST_FORMAL_DECISION_COVERAGE",
                "decision_id": event.get("decision_id"),
                "decision_time_beijing": event.get("decision_time_beijing", ""),
            }

    executions = _executions(root)
    buys = [
        x for x in executions
        if str(x.get("code") or "").strip() == code
        and str(x.get("side") or x.get("action") or "").upper() == "BUY"
        and (
            str(x.get("linked_decision_id") or "") == str(trial.get("decision_id") or "")
            or (bool(hypothesis) and str(x.get("hypothesis_id") or "") == hypothesis)
        )
    ]
    sells = [x for x in executions if str(x.get("code") or "").strip() == code and str(x.get("side") or x.get("action") or "").upper() == "SELL"]
    start = parse_time(trial.get("decision_time_beijing")) or datetime.min.replace(tzinfo=SHANGHAI)
    eligible = [x for x in sells if (parse_time(x.get("confirmed_at_beijing") or x.get("executed_at_beijing") or x.get("execution_date")) or datetime.min.replace(tzinfo=SHANGHAI)) >= start]
    # The canonical decision may be recorded after an already-linked, user-confirmed
    # execution.  Attribution identity, not recording time, owns the opening lot.
    buy_qty = sum(float(x.get("quantity") or 0) for x in buys)
    # An unlinked same-symbol sale can be attributed only when this is the
    # sole executed opening lot for the symbol. Otherwise a sale may belong
    # to another Trial, Confirm tranche, or pre-existing position.
    other_buys = [
        x for x in executions
        if str(x.get("code") or "").strip() == code
        and str(x.get("side") or x.get("action") or "").upper() == "BUY"
        and x not in buys
    ]
    explicit_sells = [
        x for x in eligible
        if str(x.get("linked_decision_id") or "") == str(trial.get("decision_id") or "")
        or (bool(hypothesis) and str(x.get("hypothesis_id") or "") == hypothesis)
    ]
    # Explicitly linked exits remain valid even when other, unrelated sales
    # exist. A later formal decision may own the sell event even though it does
    # not repeat the opening Trial hypothesis. When the opening lot is unique,
    # the complete sell quantity is still an unambiguous batch fact; rejecting
    # it merely because the later decision id differs leaves a real exit
    # permanently overdue. Multiple opening lots remain fail-closed below.
    attributable_sells = (
        eligible if len(buys) == 1 and not other_buys else explicit_sells
    )
    sell_qty = sum(float(x.get("quantity") or 0) for x in attributable_sells)
    if code and attributable_sells and buy_qty > 0 and sell_qty >= buy_qty:
        return {"status": "RESOLVED", "resolution_reason": "CONFIRMED_COMPLETE_SELL", "resolution_event_ids": [x.get("event_id") for x in attributable_sells], "resolution_time_beijing": max((x.get("confirmed_at_beijing") or x.get("executed_at_beijing") or x.get("execution_date") or "") for x in attributable_sells)}
    return None

def _completion_explicit_exit(root: Path, trial: dict[str, Any], executions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve only a uniquely linked, fully sold completion Trial lot.

    Same-symbol sales or later unlinked decisions cannot close this Trial.
    """
    decision_id = str(trial.get("decision_id") or "")
    code = str(trial.get("candidate_code") or "")
    opening_id = str(trial.get("_linked_execution_id") or "")
    buys = [x for x in executions if str(x.get("linked_decision_id") or "") == decision_id
            and str(x.get("code") or "") == code
            and str(x.get("side") or "").upper() == "BUY"]
    if len(buys) != 1 or str(buys[0].get("event_id") or "") != opening_id:
        return None  # Multiple lots cannot be allocated safely by completion identity alone.
    quantity = float(buys[0].get("quantity") or 0)
    if quantity <= 0:
        return None
    sells = [x for x in executions if str(x.get("linked_decision_id") or "") == decision_id
             and str(x.get("code") or "") == code
             and str(x.get("side") or "").upper() == "SELL"]
    opening_time = parse_time(buys[0].get("confirmed_at_beijing") or buys[0].get("executed_at_beijing") or buys[0].get("execution_date"))
    if opening_time is None or any(
        (parse_time(x.get("confirmed_at_beijing") or x.get("executed_at_beijing") or x.get("execution_date")) or
         datetime.min.replace(tzinfo=SHANGHAI)) < opening_time for x in sells
    ):
        return None
    sold = sum(float(x.get("quantity") or 0) for x in sells)
    if not sells or sold < quantity:
        return None
    return {"status": "RESOLVED", "resolution_reason": "EXPLICIT_COMPLETION_LINKED_COMPLETE_SELL",
            "resolution_event_ids": [x.get("event_id") for x in sells]}


def _completion_followup_resolution(root: Path, trial: dict[str, Any]) -> dict[str, Any] | None:
    """Accept only a later decision explicitly linked to the completion lot."""
    decision_id = str(trial.get("decision_id") or "")
    opening_id = str(trial.get("_linked_execution_id") or "")
    code = str(trial.get("candidate_code") or "")
    if not decision_id or not opening_id or not code:
        return None
    directory = root / "events" / "decisions"
    candidates = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        event = _load(path, {})
        if str(event.get("candidate_code") or (event.get("formal_decision") or {}).get("candidate_code") or "") != code:
            continue
        links = {
            str(event.get("linked_decision_id") or ""),
            str(event.get("prior_decision_id") or ""),
            str(event.get("source_trade_event_id") or ""),
            str(event.get("linked_trade_event_id") or ""),
        }
        if decision_id not in links and opening_id not in links:
            continue
        when = parse_time(event.get("decision_time_beijing"))
        if when is None:
            continue
        formal = event.get("formal_decision") or {}
        text = str(formal.get("lifecycle") or event.get("lifecycle") or "")
        if event.get("hypothesis_closed") or str(formal.get("hypothesis_status") or "").upper() == "CLOSED":
            status = "FAILED_EXIT_EVALUATION" if any(token in text for token in ("退出", "失败")) else "RESOLVED"
        elif "Confirm" in text or "确认部署" in text:
            status = "CONFIRM_EVALUATION"
        else:
            continue
        candidates.append((when, status, event))
    if not candidates:
        return None
    _, status, event = max(candidates, key=lambda item: item[0])
    return {"status": status, "decision_id": event.get("decision_id"),
            "decision_time_beijing": event.get("decision_time_beijing", "")}


def build_lifecycle_projection(root: Path, as_of_market_date: str | None = None) -> dict[str, Any]:
    calendar = _load(root / "config" / "market" / "a_share_trading_calendar_2026.json", {})
    current = _load(root / "data" / "state" / "CURRENT.json", {})
    market_date = as_of_market_date or str(current.get("market_date") or "")
    try:
        as_of = date.fromisoformat(market_date)
    except ValueError:
        as_of = datetime.now(SHANGHAI).date()
    executions = _executions(root)
    trials = []
    seen_opening_events: set[str] = set()
    for decision in (
        _formal_decisions(root)
        + _completion_trial_decisions(root, executions)
        + _state_sync_trial_decisions(root, executions)
    ):
        candidate_code = str(decision.get("candidate_code") or "").strip()
        # A Trial lifecycle starts from an executed BUY only.  A formal
        # decision may also be linked to a SELL (for example a risk-reduction
        # execution); that exit event must never manufacture a new Trial.
        linked = [
            x for x in executions
            if (
                str(x.get("side") or x.get("action") or "").upper() == "BUY"
                and (not candidate_code or str(x.get("code") or "").strip() == candidate_code)
                and (
                    str(x.get("linked_decision_id") or "") == str(decision.get("decision_id") or "")
                    or (x.get("hypothesis_id") and x.get("hypothesis_id") == decision.get("hypothesis_id"))
                    or (decision.get("_completion_trial") and str(x.get("event_id") or "") == str(decision.get("_linked_execution_id") or ""))
                )
            )
        ]
        if decision.get("_completion_trial") or decision.get("_state_sync_trial"):
            linked = [x for x in linked if x.get("event_id") == decision.get("_linked_execution_id")]
        if not linked:
            # A decision alone is not a real position lifecycle.
            continue
        execution = min(linked, key=lambda x: str(x.get("execution_date") or x.get("confirmed_at_beijing") or ""))
        opening_event_id = str(execution.get("event_id") or "")
        if opening_event_id and opening_event_id in seen_opening_events:
            continue
        if opening_event_id:
            seen_opening_events.add(opening_event_id)
        start = str(execution.get("execution_date") or str(execution.get("confirmed_at_beijing") or "")[:10])
        try:
            start_date = date.fromisoformat(start)
        except ValueError:
            continue
        t_plus = trading_day_offset(start_date, as_of, calendar)
        mandatory = add_trading_days(start_date, 3, calendar)
        decision_with_due = dict(decision, mandatory_decision_market_date=mandatory.isoformat())
        resolution = ((_completion_followup_resolution(root, decision)
                       or _completion_explicit_exit(root, decision, executions)
                       # A completion trial can be closed by a later,
                       # separately linked sell when it is the only opening
                       # lot. Reuse the canonical attribution guard.
                       or _resolution(root, decision_with_due, as_of))
                      if decision.get("_completion_trial") else _resolution(root, decision_with_due, as_of))
        resolved = resolution is not None
        trials.append({
            "source_decision_id": decision.get("decision_id"),
            "source_trade_event_id": execution.get("event_id"),
            "hypothesis_id": decision.get("hypothesis_id"),
            "hypothesis_link_status": "RESOLVED" if decision.get("hypothesis_id") else "UNRESOLVED",
            "security_code": decision.get("candidate_code") or execution.get("code"),
            "security_name": decision.get("candidate_name") or execution.get("name"),
            "lifecycle": "Trial",
            "lifecycle_status": "RESOLVED" if resolved else "ACTIVE_TRIAL",
            "hypothesis_status": resolution.get("status") if resolution else "OPEN",
            "start_market_date": start_date.isoformat(),
            "current_market_date": market_date,
            "current_t_plus": t_plus,
            "mandatory_decision_market_date": mandatory.isoformat(),
            "decision_due": bool(not resolved and t_plus is not None and as_of >= mandatory),
            "overdue": bool(not resolved and as_of > mandatory),
            "next_lifecycle_node": "RESOLVED" if resolved else "TRADE_MANDATORY_LIFECYCLE_NODE:T+3",
            "resolution": resolution,
            "decision_coverage_status": (
                "RESOLVED_BY_LATER_FORMAL_DECISION" if resolution and resolution.get("resolution_reason") == "LATEST_FORMAL_DECISION_COVERAGE"
                else "RESOLVED_BY_EXECUTION" if resolution
                else "NO_LATER_FORMAL_COVERAGE"
            ),
            "lifecycle_status_explanation": (
                "已由后续正式判断覆盖，仍保留历史Trial身份；不自动生成交易动作。"
                if resolution and resolution.get("resolution_reason") == "LATEST_FORMAL_DECISION_COVERAGE"
                else "已由可归属的完整卖出事实结案；不自动生成交易动作。"
                if resolution
                else "尚未找到该Trial在强制节点后的可归属正式决议或完整卖出事实；状态保持开放，逾期只表示应补做正式决议。"
            ),
            "execution_status": execution.get("execution_status"),
        })
    due = [x for x in trials if x["decision_due"]]
    return {
        "schema_version": "1.0",
        "status": "READY",
        "as_of_market_date": market_date,
        "active_lifecycles": trials,
        "active_trial_count": sum(x["lifecycle_status"] == "ACTIVE_TRIAL" for x in trials),
        "decision_due_count": len(due),
        "overdue_count": sum(x["overdue"] for x in due),
        "next_mandatory_node": min((x["mandatory_decision_market_date"] for x in due), default=""),
        "calendar_path": "config/market/a_share_trading_calendar_2026.json",
        "decision_boundary": "只从正式决策与真实成交事实恢复已建立的Trial生命周期；不从聊天、持仓代码或研究观察节点推导交易动作。",
        "node_semantics": "TRADE_MANDATORY_LIFECYCLE_NODE与RESEARCH/REVIEW_OBSERVATION_NODE分离；T+3只表示必须形成正式决议，不生成订单。",
        "read_only": True,
    }

