from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "state"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _risk_zone(value: Any) -> str:
    """Return only MASTER-authorized stable risk states.

    -10% is an enhanced-review boundary inside RISK_CONTROL, not a fourth state.
    """
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if pct <= -8:
        return "RISK_CONTROL"
    if pct <= -5:
        return "RISK_OBSERVATION"
    return "NORMAL"


def _enhanced_risk_review(value: Any) -> bool:
    try:
        return float(value) <= -10
    except (TypeError, ValueError):
        return False


def _display(name: Any, code: Any) -> str:
    return f"{name}（{code}）" if name and code else str(code or name or "未知标的")


def _material_unreconciled_account_changes(account: dict) -> list[dict]:
    ignored = {"total_asset", "stock_market_value", "market_value", "holding_pnl", "daily_pnl", "daily_pnl_pct"}
    cutoff = datetime.now(TZ) - timedelta(minutes=30)
    rows = []
    for event in account.get("account_change_events_after_confirmed_at") or []:
        if str(event.get("reconciliation_status") or "").upper() != "UNRECONCILED_ACCOUNT_CHANGE":
            continue
        obj = str(event.get("object") or "").strip()
        if obj in ignored:
            continue
        try:
            event_time = datetime.fromisoformat(str(event.get("event_time") or event.get("occurred_at") or "").replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=TZ)
            event_time = event_time.astimezone(TZ)
        except ValueError:
            continue
        if event_time < cutoff:
            continue
        if event.get("code") and abs(float(event.get("quantity_delta") or 0)) > 0:
            rows.append(event)
        elif obj == "cash" and abs(float(event.get("amount_delta") or 0)) >= 10:
            rows.append(event)
    return rows


def _quality(row: dict) -> str:
    return str(row.get("quality_status") or row.get("status") or "").upper()


def _latest_trade_is_recent(account: dict, max_age_minutes: int = 45) -> bool:
    trades = [x for x in (account.get("trades") or []) if x.get("trade_time")]
    if not trades:
        return False
    try:
        latest = max(datetime.fromisoformat(str(x.get("trade_time")).replace("Z", "+00:00")) for x in trades)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=TZ)
        latest = latest.astimezone(TZ)
    except ValueError:
        return False
    return datetime.now(TZ) - latest <= timedelta(minutes=max_age_minutes)


def _build_trigger(current: dict, account: dict, e2e: dict, equity: dict, prior: dict, ranking: dict, delta: dict) -> dict:
    now = _now()
    e2e_status = str(e2e.get("status") or "BLOCKED").upper()
    risk = ((((e2e.get("components") or {}).get("risk") or {}).get("etf_strategy_risk_pct")))
    if risk is None:
        risk = ((equity.get("summary") or {}).get("known_net_current_strategy_return_pct"))
    zone = _risk_zone(risk)
    prior_zone = str(prior.get("risk_zone") or "")
    event_type = ""
    applicable = ""
    evidence_time = str(current.get("captured_at") or account.get("updated_at") or now)
    evidence_change = ""
    account_changes = _material_unreconciled_account_changes(account)
    if account_changes:
        event = account_changes[-1]
        event_type = "ACCOUNT_STRUCTURE_CHANGED"
        applicable = str(event.get("object") or event.get("code") or "")
        evidence_time = str(event.get("event_time") or evidence_time)
        evidence_change = str(event.get("change_summary") or event.get("reconciliation_status") or "confirmed account change")
    elif current.get("last_trade_event_id") and current.get("last_trade_event_id") != prior.get("last_trade_event_id") and _latest_trade_is_recent(account):
        event_type = "TRADE_CONFIRMED"
        applicable = str(current.get("last_trade_event_id"))
        evidence_change = "new confirmed trade event"
    elif prior_zone and zone != prior_zone:
        event_type = "RISK_BOUNDARY_CROSSED"
        evidence_change = f"{prior_zone}→{zone}"
    elif prior.get("e2e_status") in {"DEGRADED", "BLOCKED"} and e2e_status == "READY" and prior.get("pending_trigger"):
        event_type = "E2E_RECOVERED"
        evidence_change = "E2E recovered to READY with pending trigger"
    else:
        items = delta.get("items") or []
        material = [x for x in items if x.get("decision_reassessment_required") or x.get("decision_impact")]
        if material:
            event_type = "MATERIAL_EVIDENCE_CHANGED"
            applicable = str(material[0].get("code") or "")
            evidence_change = "research evidence marked as decision-relevant"
    if not event_type:
        return {
            "schema_version": "1.1", "generated_at": now, "status": "NO_NEW_TRIGGER",
            "trigger_type": "", "triggered_at": "", "applicable_object": "",
            "previous_decision_id": str((account.get("formal_action") or {}).get("decision_id") or ""),
            "evidence_change": "", "evidence_time": evidence_time,
            "e2e_status": e2e_status, "requires_formal_reassessment": False,
            "pending_trigger": False, "idempotency_key": "",
            "risk_zone": zone, "enhanced_risk_review_required": _enhanced_risk_review(risk),
            "last_trade_event_id": str(current.get("last_trade_event_id") or ""),
            "safety_boundary": "确定性识别仅生成重评请求，不生成金额、份额或交易动作。", "read_only": True,
        }
    previous_decision = str((account.get("formal_action") or {}).get("decision_id") or "")
    key = "|".join([event_type, applicable, evidence_time[:16], previous_decision, str(current.get("last_trade_event_id") or "")])
    already = key == str(prior.get("idempotency_key") or "")
    blocked = e2e_status == "BLOCKED"
    return {
        "schema_version": "1.1", "generated_at": now,
        "status": "ALREADY_RECORDED" if already else ("PENDING" if blocked else "TRIGGERED"),
        "trigger_type": event_type, "triggered_at": "" if already else now,
        "applicable_object": applicable, "previous_decision_id": previous_decision,
        "evidence_change": evidence_change, "evidence_time": evidence_time,
        "e2e_status": e2e_status, "requires_formal_reassessment": not already,
        "pending_trigger": True if blocked or (not already and e2e_status in {"READY", "DEGRADED"}) else bool(prior.get("pending_trigger")),
        "idempotency_key": key, "risk_zone": zone, "enhanced_risk_review_required": _enhanced_risk_review(risk),
        "last_trade_event_id": str(current.get("last_trade_event_id") or ""),
        "cooldown_rule": "同一事件类型、对象、证据窗口、上一正式决策不重复生成；普通10分钟行情变化不触发。",
        "safety_boundary": "确定性识别仅生成重评请求，不生成金额、份额或交易动作。", "read_only": True,
    }


def _execution_constraints(e2e_status: str, risk_zone: str, enhanced_review: bool) -> list[str]:
    constraints = []
    if e2e_status == "BLOCKED":
        constraints.append("E2E_BLOCKED_FORMAL_AMOUNT_OR_SHARE_DECISION_REQUIRES_MISSING_CRITICAL_FACT")
    if risk_zone == "RISK_OBSERVATION":
        constraints.append("MASTER_RISK_OBSERVATION_PERMISSION_APPLIES")
    elif risk_zone == "RISK_CONTROL":
        constraints.append("MASTER_RISK_CONTROL_PERMISSION_APPLIES")
    if enhanced_review:
        constraints.append("MASTER_MINUS_10_ENHANCED_REVIEW_BOUNDARY")
    return constraints


def _build_ranking(current: dict, account: dict, e2e: dict, equity: dict, prior: dict) -> dict:
    """Build a complete comparison universe without creating trading permission.

    The machine layer enumerates capital uses and data availability. It must not
    remove legal opportunities because of risk/E2E state, create a hidden risk
    state, or preselect cash/a top candidate before the MASTER decision chain.
    """
    now = _now()
    e2e_status = str(e2e.get("status") or "BLOCKED").upper()
    risk = ((((e2e.get("components") or {}).get("risk") or {}).get("etf_strategy_risk_pct")))
    if risk is None:
        risk = ((equity.get("summary") or {}).get("known_net_current_strategy_return_pct"))
    risk_zone = _risk_zone(risk)
    enhanced_review = _enhanced_risk_review(risk)
    constraints = _execution_constraints(e2e_status, risk_zone, enhanced_review)

    latest = str(current.get("latest_snapshot") or "")
    snapshot = _read(ROOT / latest, {}) if latest else {}
    rows = [x for x in (snapshot.get("rows") or []) if isinstance(x, dict) and x.get("asset_class") == "ETF"]
    held_etf_positions = {
        str(x.get("code")): x
        for x in account.get("positions") or []
        if str(x.get("asset_type") or "").upper() == "ETF" and float(x.get("quantity") or 0) > 0
    }
    held_stock_positions = [
        x for x in account.get("positions") or []
        if str(x.get("asset_type") or "").upper() == "STOCK" and float(x.get("quantity") or 0) > 0
    ]

    comparison = [{
        "display_name": "现金", "code": None, "category": "CASH",
        "eligibility": "COMPARISON_AVAILABLE",
        "data_availability": "READY",
        "reason": "等待价值与风险缓冲属于合法资本用途；是否优于其他用途由MASTER完整判断决定。",
        "execution_constraints": constraints,
        "action_boundary": "不自动成为唯一主候选或0元结论",
    }]

    seen_etf_codes = set()
    for row in rows:
        code = str(row.get("symbol") or row.get("code") or "")
        if not code:
            continue
        seen_etf_codes.add(code)
        quality = _quality(row) or "UNKNOWN"
        usable = quality in {"PASS", "DEGRADED", "PARTIAL"}
        held = code in held_etf_positions
        position = held_etf_positions.get(code) or {}
        comparison.append({
            "display_name": _display(row.get("name") or position.get("name"), code),
            "code": code,
            "category": "HELD_ETF" if held else "OBSERVED_ETF",
            "eligibility": "HOLDING_COMPARISON" if held else "OPPORTUNITY_COMPARISON",
            "data_availability": quality if usable else "UNAVAILABLE",
            "data_quality_status": quality,
            "reason": (
                "当前持仓资本用途，继续比较持有、合法释放与其他用途。"
                if held else
                "观察ETF持续参加机会扫描；风险许可只约束实际新增，不删除候选。"
            ),
            "quantity": position.get("quantity") if held else None,
            "market_value": position.get("market_value") if held else None,
            "releasable_capital_role": "CURRENT_CAPITAL_CAN_BE_EVALUATED_FOR_RELEASE" if held else None,
            "comparison_basis": ["价格/结构", "承接", "相对反馈", "风险收益", "资本占用效率"],
            "execution_constraints": constraints + ([] if usable else ["CURRENT_MARKET_EVIDENCE_UNAVAILABLE"]),
            "action_boundary": "数据不可用只限制基于该证据形成动作，不允许对象从正式扫描框架静默消失。",
        })

    for code, position in held_etf_positions.items():
        if code in seen_etf_codes:
            continue
        comparison.append({
            "display_name": _display(position.get("name"), code),
            "code": code,
            "category": "HELD_ETF",
            "eligibility": "HOLDING_COMPARISON",
            "data_availability": "UNAVAILABLE",
            "data_quality_status": "MISSING_FROM_LATEST_SNAPSHOT",
            "reason": "持仓ETF必须保留在资本比较全集；当前行情缺失时显式标记数据不可用。",
            "quantity": position.get("quantity"), "market_value": position.get("market_value"),
            "releasable_capital_role": "CURRENT_CAPITAL_CAN_BE_EVALUATED_FOR_RELEASE",
            "execution_constraints": constraints + ["CURRENT_MARKET_EVIDENCE_UNAVAILABLE"],
            "action_boundary": "不得因行情缺失静默删除持仓。",
        })

    for position in held_stock_positions:
        code = str(position.get("code") or "")
        comparison.append({
            "display_name": _display(position.get("name"), code),
            "code": code,
            "category": "ACCOUNT_STOCK",
            "eligibility": "CAPITAL_USE_COMPARISON",
            "data_availability": "ACCOUNT_FACT_READY",
            "reason": "账户个股属于当前资本用途，参与下一单位资本与可释放资本比较；不因ETF机会自动卖出。",
            "quantity": position.get("quantity"), "market_value": position.get("market_value"),
            "releasable_capital_role": "CURRENT_CAPITAL_CAN_BE_EVALUATED_FOR_RELEASE",
            "execution_constraints": constraints,
            "action_boundary": "旧仓卖出与新机会买入必须分别通过各自MASTER完整决策链。",
        })

    return {
        "schema_version": "1.1", "generated_at": now, "as_of": current.get("captured_at") or now,
        "e2e_status": e2e_status, "risk_zone": risk_zone,
        "enhanced_risk_review_required": enhanced_review,
        "top_candidate": None,
        "candidate_selection_status": "REQUIRES_MASTER_DECISION",
        "next_unit_capital_use": "由ChatGPT按MASTER对现金、全部持仓ETF、全部观察ETF、账户个股及可释放资本重新比较；机器不预选唯一主候选。",
        "ordered_candidates": comparison,
        "comparison_universe": comparison,
        "excluded_candidates": [],
        "execution_constraints": constraints,
        "key_reason": "机器层只枚举完整资本比较全集与数据/执行约束；风险状态、E2E或单对象数据失败不得机械删除合法机会。",
        "previous_top_candidate": prior.get("top_candidate"),
        "order_semantics": "ENUMERATION_ONLY_NOT_RANKING",
        "read_only": True,
        "safety_boundary": "资本比较不替代唯一主候选，不自动生成Trial、Confirm、金额、卖出份额或订单。",
    }


def build(root: Path = ROOT) -> dict:
    current = _read(root / "data/state/CURRENT.json", {})
    account = _read(root / "data/state/account_fact.json", {})
    e2e = _read(root / "data/state/e2e_status.json", {})
    equity = _read(root / "data/state/etf_strategy_equity.json", {})
    delta = _read(root / "data/state/research_evidence_delta.json", {})
    prior_trigger = _read(root / "data/state/decision_trigger.json", {})
    prior_ranking = _read(root / "data/state/capital_efficiency_ranking.json", {})
    ranking = _build_ranking(current, account, e2e, equity, prior_ranking)
    trigger = _build_trigger(current, account, e2e, equity, prior_trigger, ranking, delta)
    _write(root / "data/state/decision_trigger.json", trigger)
    _write(root / "data/state/capital_efficiency_ranking.json", ranking)
    return {"decision_trigger": trigger, "capital_efficiency_ranking": ranking}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
