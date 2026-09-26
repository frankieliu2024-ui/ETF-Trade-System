from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from notification_materiality_guard import decision_critical_blockage

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
TZ = timezone(timedelta(hours=8))
PUSHPLUS_URL = "https://www.pushplus.plus/send"
HISTORY_LIMIT = 50
OPPORTUNITY_STATUSES = {"无机会", "观察机会", "Trial机会", "Confirm机会"}
RISK_PERMISSIONS = ("禁止新增", "允许Confirm", "允许Trial")
FORMAL_EVENT_MAX_AGE_MINUTES = 45
ACCOUNT_EVENT_MAX_AGE_MINUTES = 30
MIN_UNEXPLAINED_CASH_DELTA_YUAN = 10.0
TRADING_CALENDAR = ROOT / "config" / "market" / "a_share_trading_calendar_2026.json"

ACTIVE_REPORT_TYPES = {"ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW"}
HISTORICAL_REPORT_TYPES = {"ETF_FORMAL_DECISION"}
REPORT_REQUEST_DIR = ROOT / "requests" / "report_delivery"
REPORT_HANDOFF_DIR = ROOT / "requests" / "report_handoff"

def validate_report_delivery_request(request: dict) -> tuple[bool, str]:
    """Validate a completed active formal report before shared delivery."""
    required = ("schema_version", "channel", "report_type", "report_id", "task_id", "task_run_id",
                "generated_at", "effective_market_date", "source_actor", "source_reference",
                "title", "summary", "full_content", "content_hash", "idempotency_key")
    missing = [key for key in required if not str(request.get(key) or "").strip()]
    if missing: return False, "missing:" + ",".join(missing)
    if request.get("channel") != "REPORT": return False, "channel_must_be_REPORT"
    if request.get("report_type") not in ACTIVE_REPORT_TYPES: return False, "unsupported_report_type"
    if request.get("delivery_mode", "FULL_REPORT") != "FULL_REPORT": return False, "delivery_mode_must_be_FULL_REPORT"
    if request.get("no_trade_authority") is not True: return False, "no_trade_authority_must_be_true"
    expected_hash = hashlib.sha256(str(request.get("full_content")).encode("utf-8")).hexdigest()
    if str(request.get("content_hash")) != expected_hash: return False, "content_hash_mismatch"
    return True, ""

def build_report_delivery_request(*, task_id: str, task_run_id: str, report_id: str,
                                  report_type: str,
                                  effective_market_date: str, title: str, summary: str,
                                  full_content: str, source_reference: str,
                                  idempotency_key: str, generated_at: str,
                                  source_actor: str = "ChatGPT") -> dict:
    """Build the existing REPORT contract at its canonical delivery owner.

    This is schema construction only: it does not send, persist, or infer review
    facts. Producers must provide the task/run identity and frozen report body.
    """
    body = str(full_content)
    request = {
        "schema_version": "1.0", "channel": "REPORT",
        "report_type": str(report_type), "report_id": str(report_id),
        "task_id": str(task_id), "task_run_id": str(task_run_id),
        "generated_at": str(generated_at),
        "effective_market_date": str(effective_market_date),
        "source_actor": str(source_actor), "source_reference": str(source_reference),
        "title": str(title), "summary": str(summary), "full_content": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "idempotency_key": str(idempotency_key),
        "delivery_mode": "FULL_REPORT", "no_trade_authority": True,
    }
    valid, reason = validate_report_delivery_request(request)
    if not valid:
        raise ValueError("invalid canonical REPORT request: " + reason)
    return request


def validate_report_handoff(handoff: dict) -> tuple[bool, str]:
    """Validate the minimal delivery-only envelope from a Scheduled Review."""
    required = ("schema_version", "report_type", "task_id", "task_run_id",
                "generated_at", "effective_market_date", "title", "summary", "full_content")
    missing = [key for key in required if not str(handoff.get(key) or "").strip()]
    if missing:
        return False, "missing:" + ",".join(missing)
    if handoff.get("schema_version") != "1.0":
        return False, "unsupported_handoff_schema"
    if handoff.get("report_type") not in ACTIVE_REPORT_TYPES:
        return False, "unsupported_report_type"
    return True, ""


def build_report_from_handoff(handoff: dict) -> dict:
    """Project a delivery-only handoff into the canonical REPORT contract in memory."""
    valid, reason = validate_report_handoff(handoff)
    if not valid:
        raise ValueError("invalid REPORT handoff: " + reason)
    report_type = str(handoff["report_type"])
    task_run_id = str(handoff["task_run_id"])
    return build_report_delivery_request(
        task_id=str(handoff["task_id"]),
        task_run_id=task_run_id,
        report_id=f"{report_type.lower()}:{task_run_id}",
        report_type=report_type,
        effective_market_date=str(handoff["effective_market_date"]),
        title=str(handoff["title"]),
        summary=str(handoff["summary"]),
        full_content=str(handoff["full_content"]),
        source_reference=f"scheduled-report-handoff:{task_run_id}",
        idempotency_key=f"{report_type}:{task_run_id}",
        generated_at=str(handoff["generated_at"]),
        source_actor="Scheduled Review Actor",
    )


def _report_handoff_path() -> Path | None:
    raw_path = os.environ.get("REPORT_HANDOFF_PATH", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else ROOT / path


def _report_delivery_path() -> Path | None:
    raw_path = os.environ.get("REPORT_DELIVERY_PATH", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else ROOT / path


def report_delivery_validation_error() -> str | None:
    """Return an auditable validation error for an explicitly triggered REPORT."""
    handoff_path = _report_handoff_path()
    if handoff_path is not None:
        if not handoff_path.is_file():
            return "missing_report_handoff_path:" + str(handoff_path)
        handoff = read_json(handoff_path, {})
        valid, reason = validate_report_handoff(handoff)
        return None if valid else "invalid_report_handoff:" + reason
    path = _report_delivery_path()
    if path is None:
        return None
    if not path.is_file():
        return "missing_report_path:" + str(path)
    request = read_json(path, {})
    valid, reason = validate_report_delivery_request(request)
    return None if valid else "invalid_report:" + reason


def report_delivery_event() -> dict | None:
    handoff_path = _report_handoff_path()
    if handoff_path is not None:
        handoff = read_json(handoff_path, {})
        valid, _ = validate_report_handoff(handoff)
        if not valid:
            return None
        candidates = [build_report_from_handoff(handoff)]
        paths = []
    else:
        explicit_path = _report_delivery_path()
        paths = [explicit_path] if explicit_path is not None else (
            sorted(REPORT_REQUEST_DIR.glob("*.json")) if REPORT_REQUEST_DIR.exists() else []
        )
        candidates = []
    for path in paths:
        if path is None:
            continue
        request = read_json(path, {})
        valid, _ = validate_report_delivery_request(request)
        if valid:
            candidates.append(request)
    if not candidates: return None
    request = candidates[-1]
    key = str(request["idempotency_key"])
    return {"key": f"report-delivery:{key}", "source_event_id": f"report-delivery:{key}",
            "event_type": "REPORT_DELIVERY_REQUEST", "notification_channel": "REPORT",
            "delivery_mode": "FULL_REPORT", "type": "正式报告", "title": str(request["title"]),
            "content": str(request["full_content"]), "source": str(request["source_reference"]),
            "user_severity": "正式报告", "user_action": "阅读已完成的正式ETF报告；无需交易操作",
            "report_type": str(request["report_type"]), "report_id": str(request["report_id"]),
            "task_id": str(request["task_id"]), "task_run_id": str(request["task_run_id"]),
            "idempotency_key": key, "no_trade_authority": True}

def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def now() -> datetime:
    return datetime.now(TZ)


def parse_notification_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def human_time(value: Any, *, include_date: bool = True) -> str:
    dt = parse_notification_time(value)
    if not dt:
        return "未提供"
    return dt.strftime("%Y-%m-%d %H:%M:%S") if include_date else dt.strftime("%H:%M:%S")


def minutes_between(later: Any, earlier: Any) -> float | None:
    a, b = parse_notification_time(later), parse_notification_time(earlier)
    if not a or not b:
        return None
    return max(0.0, (a - b).total_seconds() / 60.0)


def send(token: str, title: str, content: str) -> tuple[bool, dict]:
    payload = json.dumps({"token": token, "title": title, "content": content, "template": "markdown", "channel": "wechat"}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(PUSHPLUS_URL, data=payload, headers={"Content-Type": "application/json", "User-Agent": "ETF-Trade-System/1.0"}, method="POST")
    try:
        pushplus_policy = read_json(ROOT / "config" / "runtime_policy.json", {})
        timeout = float(os.environ.get("PUSHPLUS_TIMEOUT_SECONDS", pushplus_policy.get("pushplus_timeout_seconds", 15)))
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"raw": raw[:500]}
            ok = response.status == 200 and int(body.get("code", 0) or 0) == 200
            return ok, {"http_status": response.status, "pushplus_code": body.get("code"), "pushplus_message": body.get("msg")}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, {"error": type(exc).__name__, "message": str(exc)[:300]}


def display_from_code(code: str, account: dict) -> str:
    for p in account.get("positions") or []:
        if str(p.get("code") or "") == code:
            name = str(p.get("name") or "")
            return f"{name}（{code}）" if name else code
    return code or "相关交易对象"


def trade_display(event_id: str) -> str:
    event = read_json(ROOT / "events" / "trades" / f"{event_id}.json", {}) if event_id else {}
    name = str(event.get("name") or event.get("security_name") or "")
    code = str(event.get("code") or event.get("security_code") or event.get("symbol") or "")
    return f"{name}（{code}）" if name and code else (name or code or "相关交易对象")


def is_a_share_trading_day(dt: datetime) -> bool:
    local = dt.astimezone(TZ)
    if local.weekday() >= 5:
        return False
    calendar = read_json(TRADING_CALENDAR, {})
    date_text = local.date().isoformat()
    coverage_start = str(calendar.get("coverage_start") or "")
    coverage_end = str(calendar.get("coverage_end") or "")
    if not coverage_start or not coverage_end or not (coverage_start <= date_text <= coverage_end):
        return False
    return date_text not in set(calendar.get("closed_dates") or [])


def execution_confirmation_event() -> dict | None:
    recon = read_json(STATE / "execution_reconciliation.json", {})
    if str(recon.get("status") or "") != "CONFIRMATION_REQUIRED":
        return None
    matches = [x for x in (recon.get("matches") or []) if x.get("requires_user_confirmation")]
    if not matches:
        return None
    match = matches[0]
    intent = match.get("intent") or {}
    code, name = str(intent.get("code") or ""), str(intent.get("name") or "")
    target = f"{name}（{code}）" if name and code else (code or "相关ETF")
    lifecycle = str(intent.get("lifecycle") or "交易")
    side = str(intent.get("side") or "")
    date = str(match.get("suggested_execution_date") or intent.get("decision_market_date") or "")
    status = str(match.get("status") or "")
    observed = match.get("observed_account_change") or {}
    qty = observed.get("directional_quantity")
    action = "买入" if side == "BUY" else "卖出/减持"
    size_text = ""
    if isinstance(qty, (int, float)) and qty > 0:
        size_text = f"，账户净变化约{int(qty):,}份"

    if status == "MULTIPLE_OPERATIONS_REQUIRE_DETAIL":
        title = f"{target}可能有多次操作，需要补充成交明细"
        content = f"系统发现{target}与此前{lifecycle}决策有关，但现有持仓变化无法唯一还原实际成交过程。\n\n建议：请上传券商成交明细或告诉我实际买卖笔数、数量和交易日。系统不会根据最终持仓猜测成交历史。"
    elif status == "PARTIAL_EXECUTION_REQUIRES_CONFIRMATION" or str(match.get("fill_status") or "") == "PARTIAL_EXECUTION":
        title = f"{target}可能只执行了部分{lifecycle}，请确认"
        content = f"最新账户信息显示{target}{size_text}，与{date or '此前'}的{lifecycle}{action}方向一致，但规模更像部分成交。\n\n建议：请确认实际成交数量/金额和交易日；系统会按真实成交记录，不会把部分成交记成全部执行。"
    elif status == "UNLINKED_TRADE_REQUIRES_ATTRIBUTION":
        title = f"发现{target}已有成交，请确认对应哪笔决策"
        content = f"系统发现{target}已有同方向成交，但还没有明确归因到{date or '此前'}的{lifecycle}决策。\n\n建议：请确认这笔成交是否属于该决策，以及实际交易日。"
    else:
        title = f"发现{target}可能已执行{lifecycle}，请确认"
        content = f"最新账户信息显示{target}{size_text}，与{date or '此前'}的{lifecycle}{action}决策相符。\n\n建议：请确认是否按该决策执行，以及实际交易日。系统会把交易日与截图/确认日期分开记录。"
    key = f"execution-confirm:{intent.get('decision_id','')}:{code}:{status}:{date}:{qty or ''}"
    return {"key": key, "type": "成交确认", "title": title, "content": content, "source": "execution_reconciliation", "event_type": "PENDING_EXECUTION_CONFIRMATION", "related_decision_id": str(intent.get("decision_id") or ""), "security_code": code, "security_name": name, "user_severity": "需要操作", "user_action": "确认成交或补充成交明细", "confirmation_context": {"decision_id": str(intent.get("decision_id") or ""), "security_code": code, "security_name": name, "side": side, "lifecycle": lifecycle, "quantity": qty, "approx_amount_yuan": observed.get("approx_amount_yuan"), "suggested_execution_date": date, "lifecycle_t_date": match.get("suggested_lifecycle_t_date") or date}}


def _normalize_opportunity_status(decision: dict) -> str:
    status = str(decision.get("opportunity_status") or "").strip()
    if status in OPPORTUNITY_STATUSES:
        return status
    main_candidate = str(decision.get("main_candidate") or "")
    for candidate in ("Confirm机会", "Trial机会", "观察机会", "无机会"):
        if candidate in main_candidate:
            return candidate
    if "无新的主候选" in main_candidate:
        return "无机会"
    return ""


def _normalize_risk_permission(value: Any) -> str:
    text = str(value or "").strip()
    for status in RISK_PERMISSIONS:
        if status in text:
            return status
    return text


def _formal_decision_events() -> list[dict]:
    directory = ROOT / "events" / "decisions"
    rows: list[dict] = []
    for path in directory.glob("*.json") if directory.exists() else []:
        event = read_json(path, {})
        if str(event.get("event_type") or "") != "FORMAL_DECISION":
            continue
        decision = event.get("formal_decision") or {}
        event = dict(event)
        event["_opportunity_status"] = _normalize_opportunity_status(decision)
        rows.append(event)
    rows.sort(key=lambda x: str(x.get("decision_time_beijing") or x.get("recorded_at_beijing") or ""))
    return rows


def _target_for(event: dict) -> tuple[str, str, str]:
    decision = event.get("formal_decision") or {}
    code = str(event.get("candidate_code") or decision.get("candidate_code") or "")
    name = str(event.get("candidate_name") or decision.get("candidate_name") or "")
    target = f"{name}（{code}）" if name and code else str(decision.get("main_candidate") or code or "当前主候选")
    return code, name, target


def _material_holding_action(text: str) -> bool:
    value = str(text or "")
    return any(term in value for term in ("全部退出", "降低风险", "明确合法份额"))


def _compact_amount_action(text: str) -> str:
    value = str(text or "未提供")
    if "新增0元" in value and not _material_holding_action(value) and "卖出" not in value:
        return "新增0元；现有持仓维持；现金保留。"
    return value


def _formal_market_as_of(event: dict) -> str:
    comparison = event.get("comparison_snapshot") or {}
    return str(comparison.get("as_of_beijing") or event.get("price_as_of_beijing") or "")


def _formal_account_as_of(event: dict, decision: dict) -> str:
    return str(decision.get("account_as_of_beijing") or event.get("account_as_of_beijing") or "")

def _decision_notification_details(decision: dict, status: str, target: str) -> dict:
    capital = decision.get("capital_competition") or {}
    managed = decision.get("managed_position_reviews") or []
    next_use = str(capital.get("next_unit_capital_use") or "").strip()
    zero_reason = str(capital.get("zero_amount_decisive_reason") or "").strip()
    selected_reason = str(capital.get("selected_state_reason") or "").strip()
    decisive_reason = str(decision.get("decisive_reason") or "").strip() or zero_reason or selected_reason

    new_amount = capital.get("new_amount_yuan")
    action_text = str(decision.get("amount_action") or decision.get("action") or "").strip()
    if not action_text and isinstance(new_amount, (int, float)):
        if float(new_amount) <= 0:
            action_text = f"新增0元；下一单位资本：{next_use or '现金'}。"
        else:
            action_text = f"新增{float(new_amount):,.0f}元；下一单位资本：{next_use or target}。"
    if not action_text:
        action_text = "当前正式决策未要求新的交易动作。"

    release_lines = []
    for item in capital.get("capital_release_migrations") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        amount_or_quantity = str(item.get("amount_or_quantity") or "").strip()
        destination = str(item.get("destination") or "").strip()
        source_text = source + (f"（{amount_or_quantity}）" if source and amount_or_quantity else "")
        route = " → ".join(x for x in (source_text, destination) if x)
        if route:
            release_lines.append(route)
    capital_route = "；".join(release_lines) or (f"下一单位资本：{next_use}" if next_use else "")

    next_conditions = []
    for review in managed:
        if not isinstance(review, dict):
            continue
        condition = str(review.get("next_change_condition") or "").strip()
        if condition and condition not in next_conditions:
            next_conditions.append(condition)
        if len(next_conditions) >= 2:
            break
    next_validation = "；".join(next_conditions)
    if not next_validation:
        next_validation = str(decision.get("next_validation") or decision.get("next_review") or "").strip()
    if not next_validation:
        next_validation = "在下一正式复核节点重新判断机会、持仓与资本效率。"

    if status in {"Trial机会", "Confirm机会"}:
        user_action = f"如准备执行，先核对最新正式判断与金额后由用户人工下单；{action_text}"
    elif status == "观察机会":
        user_action = "当前仅进入观察，不下单；等待下一正式复核节点验证条件是否升级。"
    elif status == "无机会":
        user_action = f"当前无需新增；{action_text}"
    else:
        user_action = action_text

    return {
        "decisive_reason": decisive_reason or "正式决策发生实质变化，需按当前资本状态重新判断。",
        "current_action": action_text,
        "capital_route": capital_route,
        "next_validation": next_validation,
        "user_action": user_action,
    }


def formal_decision_change_event() -> dict | None:
    events = _formal_decision_events()
    if not events:
        return None
    latest = events[-1]
    decision = latest.get("formal_decision") or {}
    decision_time_raw = latest.get("decision_time_beijing") or decision.get("data_as_of_beijing")
    decision_time = parse_notification_time(decision_time_raw)
    if not decision_time or now() - decision_time > timedelta(minutes=FORMAL_EVENT_MAX_AGE_MINUTES):
        return None

    previous = events[-2] if len(events) > 1 else {}
    previous_decision = previous.get("formal_decision") or {}
    status = str(latest.get("_opportunity_status") or "")
    previous_status = str(previous.get("_opportunity_status") or "")
    code, name, target = _target_for(latest)
    previous_code, _, previous_target = _target_for(previous) if previous else ("", "", "")
    risk_permission = _normalize_risk_permission(decision.get("risk_permission"))
    previous_risk = _normalize_risk_permission(previous_decision.get("risk_permission"))
    amount_action = str(decision.get("amount_action") or decision.get("action") or "")
    previous_amount_action = str(previous_decision.get("amount_action") or previous_decision.get("action") or "")

    candidate_changed = bool(code and previous_code and code != previous_code)
    opportunity_level_changed = bool(status and previous_status and status != previous_status)
    opportunity_changed = bool(status) and (opportunity_level_changed or candidate_changed)
    risk_changed = bool(risk_permission and previous_risk and risk_permission != previous_risk)
    holding_action_now = _material_holding_action(amount_action)
    holding_action_changed = holding_action_now and amount_action != previous_amount_action

    if not (opportunity_changed or risk_changed or holding_action_changed):
        return None

    decision_id = str(latest.get("decision_id") or decision.get("decision_id") or "")
    details = _decision_notification_details(decision, status, target)
    decisive_reason = details["decisive_reason"]
    action_summary = _compact_amount_action(amount_action) if amount_action else details["current_action"]
    market_as_of_raw = _formal_market_as_of(latest)
    account_as_of_raw = _formal_account_as_of(latest, decision)
    notification_generated = now()
    market_age = minutes_between(decision_time_raw, market_as_of_raw)

    change_lines: list[str] = []
    if candidate_changed:
        change_lines.append(f"- **主候选**：{previous_target} → {target}")
    if opportunity_level_changed:
        change_lines.append(f"- **机会状态**：{previous_status} → {status}")
    elif opportunity_changed:
        change_lines.append(f"- **机会状态**：{status}（状态不变，主候选发生切换）")
    if risk_changed:
        change_lines.append(f"- **风险许可**：{previous_risk} → {risk_permission}")
    elif risk_permission:
        change_lines.append(f"- **风险许可**：{risk_permission}（未变化）")
    if holding_action_changed:
        change_lines.append(f"- **持仓动作**：{action_summary}")

    actionable = holding_action_changed or (status in {"Trial机会", "Confirm机会"} and opportunity_changed) or risk_permission == "禁止新增"
    freshness_policy = (read_json(ROOT / "config" / "runtime_policy.json", {}).get("interactive_decision_freshness") or {})
    fallback_max_age_minutes = float(freshness_policy.get("fallback_max_age_seconds", 900)) / 60.0
    outside_canonical_action_window = (
        actionable
        and market_age is not None
        and market_age > fallback_max_age_minutes
    )

    if holding_action_changed:
        title = "【持仓动作｜需处理】ETF持仓需要降低风险/退出"
        severity = "需要操作"
        user_action = details["user_action"] if details["user_action"] != details["current_action"] else f"按正式持仓动作人工执行；{details['current_action']}"
    elif status in {"Trial机会", "Confirm机会"} and opportunity_changed:
        title = f"【{status}】{target}"
        severity = "需要操作"
        user_action = details["user_action"]
    elif risk_changed:
        title = f"【风险许可变化】{previous_risk} → {risk_permission}"
        severity = "需要关注" if risk_permission != "禁止新增" else "需要操作"
        user_action = details["user_action"] if status in OPPORTUNITY_STATUSES else details["current_action"]
    elif status == "观察机会":
        title = f"【观察机会】{target}"
        severity = "需要关注"
        user_action = details["user_action"]
    elif status == "无机会":
        title = f"【机会变化】{previous_target or target}当前无机会"
        severity = "需要关注"
        user_action = details["user_action"]
    else:
        title = f"【正式决策变化】{target}"
        severity = "需要关注"
        user_action = details["user_action"]

    if outside_canonical_action_window:
        user_action = "该正式判断的A股行情已超出current runtime policy允许的盘中动作时效窗口；请先刷新最新行情并重新确认正式判断，再决定是否人工执行"

    timing_lines = [
        f"- **判断时点**：{human_time(decision_time_raw)}",
        f"- **行情依据**：{human_time(market_as_of_raw)}" if market_as_of_raw else "- **行情依据**：未单独记录",
    ]
    if market_age is not None:
        timing_lines.append(f"- **行情距判断**：约{market_age:.0f}分钟")
    if account_as_of_raw:
        timing_lines.append(f"- **账户依据**：{human_time(account_as_of_raw)}")
    timing_lines.append(f"- **通知生成**：{notification_generated.strftime('%Y-%m-%d %H:%M:%S')}")

    # Keep the producer output structured.  The canonical renderer below owns
    # all user-visible Markdown sections; embedding a pre-rendered body here
    # would make the renderer wrap the same sections a second time.
    content = decisive_reason

    return {
        "key": f"formal-change:{decision_id}",
        "type": "正式决策变化",
        "event_type": "FORMAL_DECISION_MATERIAL_CHANGE",
        "title": title,
        "content": content,
        "source": "formal_decision_event",
        "related_decision_id": decision_id,
        "security_code": code,
        "security_name": name,
        "user_severity": severity,
        "user_action": user_action,
        "confirmation_context": {
            "decision_id": decision_id,
            "security_code": code,
            "security_name": name,
            "opportunity_status": status,
            "previous_opportunity_status": previous_status,
            "previous_security_code": previous_code,
            "risk_permission": risk_permission,
            "previous_risk_permission": previous_risk,
            "holding_action_changed": holding_action_changed,
            "change_summary": change_lines,
            "action_summary": action_summary,
            "decisive_reason": decisive_reason,
            "current_action": details["current_action"],
            "capital_route": details["capital_route"],
            "next_validation": details["next_validation"],
            "timing_lines": timing_lines,
            "decision_time_beijing": str(decision_time_raw or ""),
            "market_as_of_beijing": market_as_of_raw,
            "account_as_of_beijing": account_as_of_raw,
            "market_age_minutes_at_decision": market_age,
        },
    }


def decision_event() -> dict | None:
    """Raw triggers only wake a canonical decision path; they do not duplicate it."""
    trigger = read_json(STATE / "decision_trigger.json", {})
    if not trigger.get("requires_formal_reassessment") or str(trigger.get("status") or "") not in {"TRIGGERED", "ALREADY_RECORDED"}:
        return None
    key = str(trigger.get("idempotency_key") or "")
    if not key or str(trigger.get("trigger_type") or "") != "E2E_RECOVERED":
        return None
    return {
        "key": f"decision:{key}",
        "type": "判断恢复",
        "event_type": "E2E_RECOVERED",
        "title": "【判断恢复】此前暂缓的正式判断可以继续",
        "content": "此前暂缓的正式判断所需信息已经恢复；系统将继续沿现有正式判断链处理，不生成新的交易指令。",
        "source": "decision_trigger",
        "user_severity": "需要关注",
        "user_action": "等待或查看后续正式判断；通知本身不代表成交。",
    }
def _meaningful_unreconciled_account_changes(account: dict) -> list[dict]:
    """Return only account deltas that can represent a real user/account action.

    Mark-to-market fields such as total_asset, market value and floating P/L move
    whenever prices move and must never be presented as unexplained trades.
    """
    rows: list[dict] = []
    ignored_mark_to_market = {"total_asset", "stock_market_value", "market_value", "holding_pnl", "daily_pnl", "daily_pnl_pct"}
    for event in account.get("account_change_events_after_confirmed_at") or []:
        if str(event.get("reconciliation_status") or "").upper() != "UNRECONCILED_ACCOUNT_CHANGE":
            continue
        event_time = parse_notification_time(event.get("event_time") or event.get("occurred_at"))
        if not event_time or now() - event_time > timedelta(minutes=ACCOUNT_EVENT_MAX_AGE_MINUTES):
            continue
        obj = str(event.get("object") or "").strip()
        code = str(event.get("code") or "").strip()
        qty_delta = event.get("quantity_delta")
        amount_delta = event.get("amount_delta")
        if obj in ignored_mark_to_market:
            continue
        if str(event.get("reconciliation_status") or "").upper() == "RECONCILED_BY_KNOWN_IPO_REGISTRATION":
            continue
        if code and isinstance(qty_delta, (int, float)) and abs(float(qty_delta)) > 0:
            rows.append(event)
            continue
        if obj == "cash" and isinstance(amount_delta, (int, float)) and abs(float(amount_delta)) >= MIN_UNEXPLAINED_CASH_DELTA_YUAN:
            rows.append(event)
    return rows


def account_confirmation_event() -> dict | None:
    account = read_json(STATE / "account_fact.json", {})
    unresolved = _meaningful_unreconciled_account_changes(account)
    if not unresolved:
        return None

    # One broker screenshot may produce both a position and cash delta. Group the
    # latest timestamp into one user message instead of sending one alert per field.
    latest_time = max(str(e.get("event_time") or e.get("occurred_at") or "") for e in unresolved)
    group = [e for e in unresolved if str(e.get("event_time") or e.get("occurred_at") or "") == latest_time]
    event_ids = sorted(str(e.get("event_id") or e.get("idempotency_key") or "") for e in group)
    digest = hashlib.sha256("|".join(event_ids).encode("utf-8")).hexdigest()[:12]

    details: list[str] = []
    for event in group:
        code = str(event.get("code") or "")
        if code:
            target = display_from_code(code, account)
            before = event.get("quantity_before")
            after = event.get("quantity_after")
            delta = event.get("quantity_delta")
            details.append(f"- **{target}持仓数量**：{before:g} → {after:g}（变化{float(delta):+g}）")
        elif str(event.get("object") or "") == "cash":
            before = float(event.get("amount_before") or 0)
            after = float(event.get("amount_after") or 0)
            delta = float(event.get("amount_delta") or 0)
            details.append(f"- **可用资金**：{before:,.2f}元 → {after:,.2f}元（变化{delta:+,.2f}元）")

    if not details:
        return None

    title = "【账户确认】发现未解释的持仓/资金变化"
    content = (
        "### 发生了什么\n"
        + "\n".join(details)
        + "\n\n这些变化目前**没有对应到已确认成交、资金划转或其他已知账户事件**。\n\n"
        + "### 你需要做什么\n"
        + "如果你刚刚有实际买卖或资金划转，请在 ChatGPT → ETF项目 → 当前交易沟通窗口告诉我；如果没有，请上传当前券商账户截图核对。\n\n"
        + f"### 账户事实时点（北京时间）\n{human_time(latest_time)}\n\n"
        + "> 仅价格涨跌造成的总资产、市值和浮动盈亏变化不会触发此通知。"
    )
    return {
        "key": f"account-change:{latest_time}:{digest}",
        "type": "账户确认",
        "event_type": "ACCOUNT_FACT_CONFIRMATION",
        "title": title,
        "content": content,
        "source": "account_fact",
        "user_severity": "需要操作",
        "user_action": "确认是否有实际成交/资金划转；无则上传账户截图",
        "confirmation_context": {"account_event_ids": event_ids, "account_event_time_beijing": latest_time},
    }


def failed_steps_text(diag: dict) -> str:
    parts = []
    for row in diag.get("failed_steps") or []:
        step = str(row.get("step") or "").strip()
        job = str(row.get("job") or "").strip()
        if step:
            parts.append(f"{job + ' / ' if job else ''}{step}")
    return "；".join(parts[:3]) or "未提供具体失败步骤"


def system_event() -> dict | None:
    blocked, _ = decision_critical_blockage()
    if not blocked:
        return None
    heal = read_json(STATE / "self_healing_status.json", {})
    classification, action = str(heal.get("classification") or ""), str(heal.get("recommended_action") or "")
    if action == "ESCALATE" or classification in {"PERSISTENT_RUNTIME_FAILURE", "CONSISTENCY_REGRESSION"}:
        system_consistency = str(heal.get("system_consistency_status") or "未知")
        runtime_health = str(heal.get("runtime_health_status") or "未知")
        title = "【真实运行异常｜影响交易判断】ETF系统运行状态异常"
        content = (
            f"发生了什么：系统运行状态连续异常，自动修复已到达安全边界。\n\n"
            f"当前已知：一致性检查={system_consistency}；行情运行状态={runtime_health}。\n\n"
            "影响：在恢复前，行情或交易判断的可靠性可能受影响。\n\n"
            "你现在需要做什么：暂缓依据系统执行新的买入/卖出判断；已有券商持仓不会被系统自动修改。\n\n"
            "系统下一步：继续按既有安全机制检查恢复；恢复后如有待处理交易判断，会再次通知。"
        )
        return {"key": f"system:selfheal:{classification}:{heal.get('checked_at') or heal.get('updated_at') or ''}", "type": "系统异常", "title": title, "content": content, "source": "self_healing_status", "user_severity": "影响交易判断", "user_action": "暂缓依据系统做新交易判断"}

    diag = read_json(STATE / "workflow_failure_diagnostic.json", {})
    if str(diag.get("recommended_action") or "") != "ESCALATE_WITH_DIAGNOSTIC":
        return None
    safety = diag.get("safety") or {}
    head_sha = str(diag.get("head_sha") or "")
    main_head_sha = str(diag.get("main_head_sha") or "")
    head_is_current_main = bool(safety.get("head_is_current_main"))
    if (head_sha and main_head_sha and head_sha != main_head_sha) or not head_is_current_main:
        return None
    workflow = str(diag.get("workflow_name") or "后台任务")
    run_id = str(diag.get("run_id") or "未知")
    classification = str(diag.get("classification") or "未知")
    steps = failed_steps_text(diag)
    changed_files = [str(x) for x in (diag.get("changed_files") or []) if x]
    changed_text = "、".join(changed_files[:3]) if changed_files else "无明确业务数据文件变更"
    if workflow == "ETF market snapshot":
        severity = "影响交易判断"; action_text = "暂缓依据系统做新的交易判断，等待行情采集恢复。"; impact = "行情采集任务失败，最新ETF/指数行情可能不完整；账户事实不会因此被自动修改。"
    elif workflow == "ETF system consistency":
        severity = "需要关注"; action_text = "暂时无需手工修复；如果你正准备依赖系统做交易判断，请先等待下一轮一致性检查结果。"; impact = "系统一致性检查失败，说明某项状态或校验未通过；不代表券商账户或实际持仓发生变化。"
    else:
        severity = "需要关注"; action_text = "暂时无需手工修改数据；如后续影响行情、账户或交易判断，系统会升级通知。"; impact = "后台维护任务失败，但当前诊断没有证据表明券商账户或持仓被改动。"
    title = f"【真实运行异常｜{severity}】{workflow}失败"
    content = f"发生了什么：{workflow}运行失败（Run {run_id}）。\n\n具体失败：{steps}。\n\n影响：{impact}\n\n涉及变更：{changed_text}。\n\n你现在需要做什么：{action_text}\n\n系统为什么没有自动修：自动处理触及安全边界，因此停止自动修改。诊断分类：{classification}。"
    return {"key": f"system:workflow:{run_id}:{classification}", "type": "系统异常", "title": title, "content": content, "source": "workflow_failure_diagnostic", "user_severity": severity, "user_action": action_text}


def close_account_event(force: bool = False) -> dict | None:
    current, account = read_json(STATE / "CURRENT.json", {}), read_json(STATE / "account_fact.json", {})
    dt = now()
    if not force and (not is_a_share_trading_day(dt) or dt.hour < 15):
        return None
    market_date = str(current.get("market_date") or "")
    if not market_date or market_date != dt.date().isoformat():
        return None
    updated, confirmed_date = str(account.get("updated_at") or ""), str(account.get("last_confirmed_market_date") or "")
    final_confirmed = False
    if confirmed_date == market_date and updated:
        try:
            final_confirmed = datetime.fromisoformat(updated).astimezone(TZ).hour >= 15
        except ValueError:
            pass
    if final_confirmed:
        return None
    content = (
        "今天是A股交易日，系统尚未取得15:00之后可确认的最终账户事实。\n\n"
        "### 你需要做\n"
        "请把**15:00收盘后的券商持仓/账户截图**上传到 **ChatGPT → ETF项目 →「ETF交易复盘」聊天窗口**。\n\n"
        "如果收盘后账户没有任何变化，也可以直接在该窗口回复：**收盘账户无变化**。"
    )
    return {"key": f"close-account:{market_date}", "type": "收盘账户", "title": "【收盘账户｜需确认】请到ETF交易复盘上传收盘截图", "content": content, "source": "account_fact", "user_severity": "需要操作", "user_action": "到ChatGPT ETF项目的「ETF交易复盘」聊天窗口上传收盘截图或确认无变化"}


def choose_event(mode: str) -> dict | None:
    if mode == "close": return close_account_event()
    if mode == "close-test": return close_account_event(force=True)
    # An explicitly triggered REPORT must deliver that exact report.
    # Other workflow_run events retain the existing INTERRUPT precedence.
    builders = (
        (report_delivery_event, execution_confirmation_event, formal_decision_change_event,
         account_confirmation_event, system_event, decision_event)
        if (_report_handoff_path() is not None or _report_delivery_path() is not None)
        else
        (execution_confirmation_event, formal_decision_change_event, account_confirmation_event,
         system_event, decision_event, report_delivery_event)
    )
    for builder in builders:
        event = builder()
        if event: return event
    return None

LIFECYCLE_STATUSES = {"CREATED", "SENT", "WAITING_CONFIRMATION", "CONFIRMED", "ARCHIVED", "EXPIRED"}
USER_ACTION_TYPES = {"成交确认", "账户确认", "收盘账户", "交易判断", "正式决策变化", "系统异常"}
NOTIFICATION_TTL_DAYS = 2


def notification_id_for(event: dict) -> str:
    source = str(event.get("source_event_id") or event.get("key") or "")
    return "notification_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


def normalize_notification(event: dict, record: dict | None = None) -> dict:
    record = record or {}
    created = str(record.get("created_at") or event.get("created_at") or now().isoformat(timespec="seconds"))
    context = event.get("confirmation_context") or {}
    return {"notification_id": str(record.get("notification_id") or event.get("notification_id") or notification_id_for(event)), "event_type": str(record.get("event_type") or event.get("event_type") or event.get("type") or "SYSTEM_EVENT"), "notification_channel": str(record.get("notification_channel") or event.get("notification_channel") or ("REPORT" if event.get("event_type") == "REPORT_DELIVERY_REQUEST" else "INTERRUPT")), "delivery_mode": str(record.get("delivery_mode") or event.get("delivery_mode") or ("FULL_REPORT" if event.get("event_type") == "REPORT_DELIVERY_REQUEST" else "COMPACT")), "source_event_id": str(record.get("source_event_id") or event.get("source_event_id") or event.get("key") or ""), "related_decision_id": str(record.get("related_decision_id") or event.get("related_decision_id") or context.get("decision_id") or ""), "security_code": str(record.get("security_code") or event.get("security_code") or context.get("security_code") or ""), "security_name": str(record.get("security_name") or event.get("security_name") or context.get("security_name") or ""), "user_severity": str(record.get("user_severity") or event.get("user_severity") or ""), "user_action": str(record.get("user_action") or event.get("user_action") or ""), "lifecycle_status": str(record.get("lifecycle_status") or record.get("status") or "CREATED"), "created_at": created, "sent_at": record.get("sent_at"), "confirmed_at": record.get("confirmed_at"), "archived_at": record.get("archived_at"), "expires_at": record.get("expires_at") or ((parse_notification_time(created) + timedelta(days=NOTIFICATION_TTL_DAYS)).isoformat(timespec="seconds") if parse_notification_time(created) else None), "title": str(record.get("title") or event.get("title") or ""), "content": str(record.get("content") or event.get("content") or ""), "source": str(record.get("source") or event.get("source") or ""), "confirmation_context": context or record.get("confirmation_context") or {}, "response": record.get("response") or {}, "last_attempted_at": record.get("last_attempted_at") or record.get("attempted_at")}


def expire_notifications(items: list[dict]) -> list[dict]:
    current = now(); out = []
    for raw in items:
        item = normalize_notification(raw, raw)
        status = str(item.get("lifecycle_status") or "")
        expiry = parse_notification_time(item.get("expires_at"))
        if status in {"SENT", "WAITING_CONFIRMATION"} and expiry and current >= expiry:
            item["lifecycle_status"] = "EXPIRED"; item["archived_at"] = item.get("archived_at") or current.isoformat(timespec="seconds")
        out.append(item)
    return out


def find_existing_notification(items: list[dict], event: dict) -> dict | None:
    source = str(event.get("source_event_id") or event.get("key") or "")
    nid = str(event.get("notification_id") or "")
    related_decision_id = str(event.get("related_decision_id") or (event.get("confirmation_context") or {}).get("decision_id") or "")
    event_type = str(event.get("event_type") or "")
    event_account_ids = set((event.get("confirmation_context") or {}).get("account_event_ids") or [])
    for item in items:
        if nid and str(item.get("notification_id") or "") == nid:
            return item
        if source and str(item.get("source_event_id") or "") == source:
            return item
        if event_type == "FORMAL_DECISION_MATERIAL_CHANGE" and related_decision_id and str(item.get("related_decision_id") or "") == related_decision_id:
            return item
        if event_type == "ACCOUNT_FACT_CONFIRMATION" and event_account_ids:
            item_ids = set((item.get("confirmation_context") or {}).get("account_event_ids") or [])
            if event_account_ids & item_ids:
                return item
    return None


def compact_recent(item: dict) -> dict:
    return {"key": item.get("source_event_id"), "type": item.get("event_type"), "title": item.get("title"), "content": item.get("content"), "source": item.get("source"), "user_severity": item.get("user_severity"), "user_action": item.get("user_action"), "status": "SENT" if item.get("lifecycle_status") in {"SENT", "WAITING_CONFIRMATION"} else item.get("lifecycle_status"), "attempted_at": item.get("last_attempted_at") or item.get("sent_at") or item.get("created_at"), "response": item.get("response") or {}, "notification_id": item.get("notification_id"), "lifecycle_status": item.get("lifecycle_status")}


def _notification_matches_intent(item: dict, match: dict) -> bool:
    """Require exact decision identity plus the notification's object/action identity."""
    intent = match.get("intent") or {}
    context = item.get("confirmation_context") or {}
    item_code = str(item.get("security_code") or context.get("security_code") or "")
    item_side = str(context.get("side") or "")
    item_lifecycle = str(context.get("lifecycle") or "")
    if item_code and str(intent.get("code") or "") != item_code:
        return False
    if item_side and str(intent.get("side") or "") != item_side:
        return False
    if item_lifecycle and str(intent.get("lifecycle") or "") != item_lifecycle:
        return False
    return True


def revalidate_pending_notifications(notifications: list[dict]) -> list[dict]:
    """Revalidate pending prompts against canonical facts using exact identity first."""
    reconciliation = read_json(STATE / "execution_reconciliation.json", {})
    matches = reconciliation.get("matches") or []
    for item in notifications:
        if str(item.get("lifecycle_status") or "").upper() != "WAITING_CONFIRMATION":
            continue
        event_type = str(item.get("event_type") or "")
        reason = ""
        if event_type == "PENDING_EXECUTION_CONFIRMATION":
            context = item.get("confirmation_context") or {}
            related_decision_id = str(item.get("related_decision_id") or context.get("decision_id") or "")
            exact = [match for match in matches if str((match.get("intent") or {}).get("decision_id") or "") == related_decision_id] if related_decision_id else []
            if related_decision_id:
                if len(exact) == 1 and _notification_matches_intent(item, exact[0]) and not bool(exact[0].get("requires_user_confirmation")):
                    reason = "exact related decision is canonically reconciled; confirmation is no longer required"
            else:
                code = str(item.get("security_code") or context.get("security_code") or "")
                side = str(context.get("side") or "")
                lifecycle = str(context.get("lifecycle") or "")
                date = str(context.get("suggested_execution_date") or context.get("execution_date") or "")
                candidates = [match for match in matches
                              if str((match.get("intent") or {}).get("code") or "") == code
                              and (not side or str((match.get("intent") or {}).get("side") or "") == side)
                              and (not lifecycle or str((match.get("intent") or {}).get("lifecycle") or "") == lifecycle)
                              and (not date or str(match.get("execution_date") or "") == date)]
                if len(candidates) == 1 and not bool(candidates[0].get("requires_user_confirmation")):
                    reason = "unique constrained legacy execution identity is reconciled"
        elif event_type == "收盘账户":
            source = str(item.get("source_event_id") or "")
            market_date = source.split(":", 1)[1] if source.startswith("close-account:") else ""
            closure = read_json(STATE / f"close_review_closure_{market_date}.json", {})
            if market_date and str(closure.get("status") or "").upper() == "CLOSED":
                reason = f"canonical close review closure completed for {market_date}"
        if reason:
            stamp = now().isoformat(timespec="seconds")
            item["lifecycle_status"] = "ARCHIVED"
            item["archived_at"] = item.get("archived_at") or stamp
            item["revalidation_reason"] = reason
    return notifications


CANONICAL_TEMPLATE_FAMILIES = {
    "PENDING_EXECUTION_CONFIRMATION": "成交确认",
    "ACCOUNT_FACT_CONFIRMATION": "账户确认",
    "E2E_RECOVERED": "判断恢复",
    "SYSTEM_EVENT": "系统阻塞",
    "CHANNEL_TEST": "测试",
    "收盘账户": "收盘账户",
}
def canonical_template_family(event: dict) -> str | None:
    event_type = str(event.get("event_type") or event.get("type") or "")
    if event_type == "REPORT_DELIVERY_REQUEST":
        return "REPORT"
    if event_type == "FORMAL_DECISION_MATERIAL_CHANGE":
        ctx = event.get("confirmation_context") or {}
        status = str(ctx.get("opportunity_status") or "")
        previous = str(ctx.get("previous_opportunity_status") or "")
        if ctx.get("holding_action_changed"):
            return "持仓动作"
        if status in {"观察机会", "Trial机会", "Confirm机会"} and status != previous:
            return status
        if status == "无机会" and status != previous:
            return "机会失效"
        if ctx.get("risk_permission") != ctx.get("previous_risk_permission"):
            return "风险许可"
        return "观察机会" if status and status != "无机会" else "机会失效"
    return CANONICAL_TEMPLATE_FAMILIES.get(event_type)
def _canonical_target(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    name = str(event.get("security_name") or ctx.get("security_name") or "")
    code = str(event.get("security_code") or ctx.get("security_code") or "")
    return f"{name}（{code}）" if name and code else (name or code or "相关对象")
def _canonical_time(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    value = ctx.get("account_event_time_beijing") or ctx.get("market_as_of_beijing") or ctx.get("decision_time_beijing")
    return human_time(value) if value else "未单独记录"
def _canonical_boundary(family: str) -> str:
    if family == "成交确认":
        return "仅确认既有成交及其归因，不生成新交易指令。"
    if family in {"观察机会", "Trial机会", "Confirm机会", "机会失效", "持仓动作", "风险许可"}:
        return "通知只转发已有正式判断；用户如需交易必须人工核对并下单，通知本身不代表成交。"
    if family in {"系统阻塞", "判断恢复"}:
        return "系统只报告当前恢复/阻塞边界，不修改MASTER、账户事实或交易权限。"
    if family == "账户确认":
        return "仅核对账户事实，不把价格波动或通知发送当作成交。"
    if family == "收盘账户":
        return "收盘提醒只请求账户事实确认，不生成交易指令。"
    return "这是通知通道测试，不代表真实行情、账户、交易或系统故障。"
def render_canonical_notification(event: dict) -> dict | None:
    family = canonical_template_family(event)
    if family is None:
        return None
    if family == "REPORT":
        return event
    rendered = dict(event)
    rendered["template_family"] = family
    target = _canonical_target(event)
    ctx = event.get("confirmation_context") or {}
    capital_route_section = f"### 资本去向\n{ctx.get('capital_route')}\n\n" if ctx.get("capital_route") else ""
    if family == "成交确认":
        title = f"【成交确认】{target}"
        body = (
            f"### 发生了什么\n{target}已有成交事实。\n\n"
            f"### 成交事实与缺口\n{event.get('content') or '成交归因信息尚未完整。'}\n\n"
            f"### 你需要做什么\n{event.get('user_action') or '确认既有成交归因'}\n\n"
            f"> {_canonical_boundary(family)}\n\n### 事实时点（北京时间）\n{_canonical_time(event)}"
        )
    elif family == "账户确认":
        title = "【账户确认】发现未解释的持仓/资金变化"
        body = (
            f"### 发生了什么\n{event.get('content') or '发现未解释的账户事实变化。'}\n\n"
            f"### 你需要做什么\n{event.get('user_action') or '确认是否存在实际成交或资金划转。'}\n\n"
            f"> {_canonical_boundary(family)}\n\n### 事实时点（北京时间）\n{_canonical_time(event)}"
        )
    elif family == "收盘账户":
        title = "【收盘账户】请确认15:00后的账户事实"
        body = f"### 发生了什么\n当前交易日收盘账户事实仍需确认。\n\n### 你需要做什么\n请上传15:00收盘后的券商持仓/账户截图，或确认收盘账户无变化。\n\n> {_canonical_boundary(family)}"
    elif family == "系统阻塞":
        title = "【系统阻塞】ETF系统当前存在影响判断的运行阻塞"
        body = f"### 发生了什么\n{event.get('content') or '系统运行状态未达到可依赖条件。'}\n\n### 你需要做什么\n暂缓依据系统执行新的交易判断，等待系统恢复。\n\n> {_canonical_boundary(family)}"
    elif family == "判断恢复":
        title = "【判断恢复】此前暂缓的正式判断可以继续"
        body = f"### 发生了什么\n{event.get('content') or '此前暂缓的正式判断所需信息已经恢复。'}\n\n### 当前边界\n系统可以继续正式判断，但不生成新的交易指令。\n\n> {_canonical_boundary(family)}"
    elif family == "风险许可":
        previous_risk = str(ctx.get("previous_risk_permission") or "未记录")
        risk = str(ctx.get("risk_permission") or "未记录")
        title = f"【风险许可】{previous_risk} → {risk}"
        change_summary = "\n".join(str(x) for x in (ctx.get("change_summary") or []) if x) or f"风险许可：{previous_risk} → {risk}"
        body = (
            f"### 发生了什么\n{change_summary}\n\n"
            f"### 当前风险许可\n{previous_risk} → {risk}\n\n"
            f"### 为什么现在值得关注\n{ctx.get('decisive_reason') or '正式风险许可发生了实质变化。'}\n\n"
            f"### 当前正式动作\n{ctx.get('current_action') or '当前正式决策未要求新的交易动作。'}\n\n"
            f"{capital_route_section}"
            f"### 你需要做什么\n{event.get('user_action') or '无明确交易动作时无需下单。'}\n\n"
            f"### 下一关注点\n{ctx.get('next_validation') or '在下一正式复核节点重新判断。'}\n\n"
            f"> {_canonical_boundary(family)}\n\n### 事实时点（北京时间）\n{_canonical_time(event)}"
        )
    elif family in {"观察机会", "Trial机会", "Confirm机会", "机会失效", "持仓动作"}:
        status = str(ctx.get("opportunity_status") or family)
        previous = str(ctx.get("previous_opportunity_status") or "")
        risk = str(ctx.get("risk_permission") or "")
        title = f"【{family}】{target}"
        change_summary = "\n".join(str(x) for x in (ctx.get("change_summary") or []) if x)
        body = (
            f"### 发生了什么\n{change_summary or f'{target}的正式判断发生变化。'}\n\n"
            f"### 当前正式状态\n{previous + ' → ' if previous and previous != status else ''}{status}"
            f"{f'；风险许可：{risk}' if risk else ''}\n\n"
            f"### 为什么现在值得关注\n{ctx.get('decisive_reason') or '正式决策形成了实质变化。'}\n\n"
            f"### 当前正式动作\n{ctx.get('current_action') or '当前正式决策未要求新的交易动作。'}\n\n"
            f"{capital_route_section}"
            f"### 你需要做什么\n{event.get('user_action') or '无明确交易动作时无需下单。'}\n\n"
            f"### 下一关注点\n{ctx.get('next_validation') or '在下一正式复核节点重新判断。'}\n\n"
            f"> {_canonical_boundary(family)}\n\n### 事实时点（北京时间）\n{_canonical_time(event)}"
        )
    elif family == "测试":
        title = "【测试】ETF系统通知中心"
        body = str(event.get("content") or "通知通道测试。")
    else:
        return None
    rendered["title"] = title
    rendered["content"] = body
    rendered["type"] = family
    return rendered

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--mode", choices=["event", "close", "close-test", "channel-test"], default="event"); args = parser.parse_args()
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip(); state_path = STATE / "notification_center.json"; state = read_json(state_path, {"schema_version": "1.0", "recent": []})
    raw_items = list(state.get("notifications") or [])
    if not raw_items: raw_items = [normalize_notification(x, x) for x in (state.get("recent") or [])]
    notifications = expire_notifications(raw_items)
    notifications = revalidate_pending_notifications(notifications)
    if args.mode == "channel-test":
        event = {"key": f"channel-test:{now().isoformat(timespec='seconds')}", "type": "测试", "title": "【测试】ETF系统通知中心", "content": "这是一条通知通道测试，不代表真实行情、账户、交易或系统故障。\n\n你现在需要做什么：无需操作。收到即表示 GitHub → PushPlus → 微信通道正常。", "source": "manual_test", "event_type": "CHANNEL_TEST", "user_severity": "测试", "user_action": "无需操作"}
    else:
        validation_error = report_delivery_validation_error()
        if validation_error:
            stamp = now().isoformat(timespec="seconds")
            state.update({
                "schema_version": "2.2",
                "updated_at": stamp,
                "last_status": "FAILED",
                "last_type": "REPORT_DELIVERY_REQUEST",
                "last_error": validation_error,
                "notifications": notifications[-HISTORY_LIMIT:],
                "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]],
                "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"],
            })
            write_json(state_path, state)
            print(json.dumps({"status": "FAILED", "event_type": "REPORT_DELIVERY_REQUEST",
                              "error": validation_error}, ensure_ascii=False))
            return 1
        event = choose_event(args.mode)
    if event:
        event = render_canonical_notification(event)
        if event is None:
            print(json.dumps({"status": "UNSUPPORTED_EVENT_TYPE", "event_type": "unknown"}, ensure_ascii=False))
            return 2
    if not event:
        state.update({"schema_version": "2.2", "updated_at": now().isoformat(timespec="seconds"), "notifications": notifications, "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]}); write_json(state_path, state); print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False)); return 0
    existing = find_existing_notification(notifications, event)
    if existing and existing.get("lifecycle_status") in {"SENT", "WAITING_CONFIRMATION", "CONFIRMED", "ARCHIVED"}:
        state.update({"schema_version": "2.2", "updated_at": now().isoformat(timespec="seconds"), "notifications": notifications, "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]}); write_json(state_path, state); print(json.dumps({"status": "ALREADY_MANAGED", "notification_id": existing.get("notification_id"), "lifecycle_status": existing.get("lifecycle_status")}, ensure_ascii=False)); return 0
    if existing and existing.get("lifecycle_status") == "EXPIRED":
        print(json.dumps({"status": "EXPIRED_REQUIRES_NEW_EVENT", "notification_id": existing.get("notification_id")}, ensure_ascii=False)); return 0
    item = normalize_notification(event, existing); item["lifecycle_status"] = "CREATED"; item["created_at"] = item.get("created_at") or now().isoformat(timespec="seconds")
    if not token:
        stamp = now().isoformat(timespec="seconds")
        item["last_attempted_at"] = stamp
        item["response"] = {"error": "PUSHPLUS_TOKEN missing"}
        item["lifecycle_status"] = "FAILED"
        notifications = [x for x in notifications if x.get("notification_id") != item["notification_id"]]
        notifications.append(item)
        state.update({"schema_version": "2.2", "updated_at": stamp, "last_status": "FAILED", "last_type": item["event_type"], "last_title": item["title"], "notifications": notifications[-HISTORY_LIMIT:], "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]}); write_json(state_path, state); print(json.dumps({"status": "FAILED", "notification_id": item["notification_id"], "lifecycle_status": "FAILED"}, ensure_ascii=False)); return 1
    ok, response = send(token, item["title"], item["content"]); stamp = now().isoformat(timespec="seconds"); item["last_attempted_at"] = stamp; item["response"] = response
    item["lifecycle_status"] = "WAITING_CONFIRMATION" if ok and item["event_type"] in {"PENDING_EXECUTION_CONFIRMATION", "成交确认", "账户确认", "ACCOUNT_FACT_CONFIRMATION", "收盘账户"} else ("SENT" if ok else "FAILED"); item["sent_at"] = stamp if ok else item.get("sent_at")
    if existing: notifications = [x for x in notifications if x.get("notification_id") != item["notification_id"]]
    notifications.append(item)
    state = {"schema_version": "2.2", "updated_at": stamp, "last_status": item["lifecycle_status"], "last_type": item["event_type"], "last_title": item["title"], "notifications": notifications[-HISTORY_LIMIT:], "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"], "policy": "只推送会改变用户关注、风险许可、机会状态、持仓动作、执行确认或系统可靠性的实质事件；总资产/市值/浮动盈亏等纯盯市变化不作为账户异常；同一实质账户事件和同一正式decision_id不得重复推送；收盘账户提醒仅在A股交易日触发。", "safety_boundary": "通知中心只转发已有正式判断，不生成交易动作，不修改MASTER、风险许可、金额或卖出份额；正式成交只能由用户确认入口提交。"}
    write_json(state_path, state); print(json.dumps(item, ensure_ascii=False)); return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
