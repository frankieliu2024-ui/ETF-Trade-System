"""Canonical, read-only projection of executed Trial lifecycles.

Lifecycle state is derived from formal decision and execution facts.  It is
not a second event store and it never creates a permission, order, or trade.
"""
from __future__ import annotations

import json
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
    directory = root / "events" / "decisions"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        event = _load(path, {})
        formal = event.get("formal_decision") or {}
        lifecycle = str(formal.get("lifecycle") or event.get("lifecycle") or "")
        if not isinstance(formal, dict) or "Trial" not in lifecycle:
            continue
        if not event.get("hypothesis_id"):
            continue
        result.append(event)
    return result


def _executions(root: Path) -> list[dict[str, Any]]:
    result = []
    directory = root / "events" / "trades"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        event = _load(path, {})
        if str(event.get("execution_status") or "").upper() == "EXECUTED":
            result.append(event)
    return result


def _resolution(root: Path, trial: dict[str, Any]) -> dict[str, Any] | None:
    hypothesis = str(trial.get("hypothesis_id") or "")
    decision_time = parse_time(trial.get("decision_time_beijing"))
    candidates = []
    directory = root / "events" / "decisions"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        event = _load(path, {})
        if event.get("decision_id") == trial.get("decision_id") or str(event.get("hypothesis_id") or "") != hypothesis:
            continue
        if decision_time and (parse_time(event.get("decision_time_beijing")) or decision_time) < decision_time:
            continue
        formal = event.get("formal_decision") or {}
        lifecycle = str(formal.get("lifecycle") or "")
        if event.get("hypothesis_closed") or str(formal.get("hypothesis_status") or "").upper() == "CLOSED":
            status = "FAILED_EXIT_EVALUATION" if ("退出" in lifecycle or "失败" in lifecycle) else "RESOLVED"
        elif "Confirm" in lifecycle:
            status = "CONFIRM_EVALUATION"
        else:
            continue
        candidates.append((parse_time(event.get("decision_time_beijing")) or datetime.min.replace(tzinfo=SHANGHAI), status, event))
    chosen = max(candidates, key=lambda x: x[0], default=None)
    if not chosen:
        return None
    _, chosen_status, chosen_event = chosen
    return {"status": chosen_status, "decision_id": chosen_event.get("decision_id"), "decision_time_beijing": chosen_event.get("decision_time_beijing", "")}


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
    for decision in _formal_decisions(root):
        linked = [x for x in executions if str(x.get("linked_decision_id") or "") == str(decision.get("decision_id") or "") or (x.get("hypothesis_id") and x.get("hypothesis_id") == decision.get("hypothesis_id"))]
        if not linked:
            # A decision alone is not a real position lifecycle.
            continue
        execution = min(linked, key=lambda x: str(x.get("execution_date") or x.get("confirmed_at_beijing") or ""))
        start = str(execution.get("execution_date") or str(execution.get("confirmed_at_beijing") or "")[:10])
        try:
            start_date = date.fromisoformat(start)
        except ValueError:
            continue
        t_plus = trading_day_offset(start_date, as_of, calendar)
        mandatory = add_trading_days(start_date, 3, calendar)
        resolution = _resolution(root, decision)
        resolved = resolution is not None
        trials.append({
            "source_decision_id": decision.get("decision_id"),
            "source_trade_event_id": execution.get("event_id"),
            "hypothesis_id": decision.get("hypothesis_id"),
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
