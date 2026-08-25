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
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if pct <= -10:
        return "RISK_CONTROL_REVIEW"
    if pct <= -8:
        return "RISK_CONTROL"
    if pct <= -5:
        return "RISK_OBSERVATION"
    return "NORMAL"


def _display(name: Any, code: Any) -> str:
    return f"{name}（{code}）" if name and code else str(code or name or "未知标的")


def _quality(row: dict) -> str:
    return str(row.get("quality_status") or row.get("status") or "").upper()


def _build_trigger(current: dict, account: dict, e2e: dict, equity: dict, prior: dict, ranking: dict, delta: dict) -> dict:
    now = _now()
    e2e_status = str(e2e.get("status") or "BLOCKED").upper()
    risk = ((equity.get("summary") or {}).get("known_net_current_strategy_return_pct"))
    zone = _risk_zone(risk)
    prior_zone = str(prior.get("risk_zone") or "")
    event_type = ""
    applicable = ""
    evidence_time = str(current.get("captured_at") or account.get("updated_at") or now)
    evidence_change = ""
    if account.get("account_change_events_after_confirmed_at"):
        event = account["account_change_events_after_confirmed_at"][-1]
        event_type = "ACCOUNT_STRUCTURE_CHANGED"
        applicable = str(event.get("object") or event.get("code") or "")
        evidence_time = str(event.get("event_time") or evidence_time)
        evidence_change = str(event.get("change_summary") or event.get("reconciliation_status") or "confirmed account change")
    elif current.get("last_trade_event_id") and current.get("last_trade_event_id") != prior.get("last_trade_event_id"):
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
            "schema_version": "1.0", "generated_at": now, "status": "NO_NEW_TRIGGER",
            "trigger_type": "", "triggered_at": "", "applicable_object": "",
            "previous_decision_id": str((account.get("formal_action") or {}).get("decision_id") or ""),
            "evidence_change": "", "evidence_time": evidence_time,
            "e2e_status": e2e_status, "requires_formal_reassessment": False,
            "pending_trigger": False, "idempotency_key": "",
            "risk_zone": zone, "last_trade_event_id": str(current.get("last_trade_event_id") or ""),
            "safety_boundary": "确定性识别仅生成重评请求，不生成金额、份额或交易动作。", "read_only": True,
        }
    previous_decision = str((account.get("formal_action") or {}).get("decision_id") or "")
    key = "|".join([event_type, applicable, evidence_time[:16], previous_decision, str(current.get("last_trade_event_id") or "")])
    already = key == str(prior.get("idempotency_key") or "")
    blocked = e2e_status == "BLOCKED"
    return {
        "schema_version": "1.0", "generated_at": now,
        "status": "ALREADY_RECORDED" if already else ("PENDING" if blocked else "TRIGGERED"),
        "trigger_type": event_type, "triggered_at": "" if already else now,
        "applicable_object": applicable, "previous_decision_id": previous_decision,
        "evidence_change": evidence_change, "evidence_time": evidence_time,
        "e2e_status": e2e_status, "requires_formal_reassessment": not already,
        "pending_trigger": True if blocked or (not already and e2e_status in {"READY", "DEGRADED"}) else bool(prior.get("pending_trigger")),
        "idempotency_key": key, "risk_zone": zone,
        "last_trade_event_id": str(current.get("last_trade_event_id") or ""),
        "cooldown_rule": "同一事件类型、对象、证据窗口、上一正式决策不重复生成；普通10分钟行情变化不触发。",
        "safety_boundary": "确定性识别仅生成重评请求，不生成金额、份额或交易动作。", "read_only": True,
    }


def _build_ranking(current: dict, account: dict, e2e: dict, equity: dict, prior: dict) -> dict:
    now = _now()
    e2e_status = str(e2e.get("status") or "BLOCKED").upper()
    risk = ((equity.get("summary") or {}).get("known_net_current_strategy_return_pct"))
    risk_zone = _risk_zone(risk)
    latest = str(current.get("latest_snapshot") or "")
    snapshot = _read(ROOT / latest, {}) if latest else {}
    rows = [x for x in (snapshot.get("rows") or []) if isinstance(x, dict) and x.get("asset_class") == "ETF"]
    held = {str(x.get("code")) for x in account.get("positions") or [] if str(x.get("asset_type") or "").upper() == "ETF" and float(x.get("quantity") or 0) > 0}
    quality_rows = [x for x in rows if _quality(x) in {"PASS", "DEGRADED", "PARTIAL"}]
    ordered = []
    excluded = []
    cash = {"display_name": "现金", "code": None, "category": "CASH", "eligibility": "AVAILABLE", "reason": "等待价值与风险缓冲可直接参与比较；不自动等同于低效率。"}
    if e2e_status == "BLOCKED":
        cash["reason"] = "E2E BLOCKED；只保留现金观察，不输出正式金额或份额。"
    ordered.append(cash)
    candidates = []
    for row in quality_rows:
        code = str(row.get("symbol") or row.get("code") or "")
        if not code:
            continue
        name = row.get("name")
        if code in held:
            candidates.append({
                "display_name": _display(name, code), "code": code, "category": "HELD_ETF",
                "eligibility": "HOLDING_COMPARISON", "reason": "当前持仓资本用途；不因排序自动生成卖出或回补动作。",
                "comparison_basis": ["当前价格与结构", "持仓压力", "相关性", "可释放资本"],
                "action_boundary": "仍需MASTER正式判断",
            })
        elif e2e_status == "BLOCKED":
            excluded.append({"display_name": _display(name, code), "reason": "E2E_BLOCKED"})
        elif risk_zone in {"RISK_CONTROL", "RISK_CONTROL_REVIEW"}:
            excluded.append({"display_name": _display(name, code), "reason": "风险控制区普通风险扩张候选不预先放行；如属风险中性迁移仍走原交易链。"})
        else:
            candidates.append({
                "display_name": _display(name, code), "code": code, "category": "OBSERVED_ETF",
                "eligibility": "OBSERVATION_COMPARISON", "reason": "进入相对比较但仍需验证Trial/Confirm最小证据与失效条件。",
                "comparison_basis": ["价格优势", "结构/修复", "相对强弱", "组合相关性", "资本占用效率"],
                "action_boundary": "排名第一不自动升级为唯一主候选",
            })
    candidates.sort(key=lambda x: (0 if x["category"] == "OBSERVED_ETF" else 1, x["display_name"]))
    ordered.extend(candidates[:5])
    top = ordered[0]["display_name"] if ordered else "现金"
    return {
        "schema_version": "1.0", "generated_at": now, "as_of": current.get("captured_at") or now,
        "e2e_status": e2e_status, "risk_zone": risk_zone,
        "top_candidate": top, "next_unit_capital_use": "现金/持仓/观察候选按原MASTER链人工比较；本结果不生成金额。",
        "ordered_candidates": ordered[:6], "excluded_candidates": excluded[:12],
        "key_reason": "两阶段：先做E2E、风险扩张、质量和可执行性硬过滤，再保留少量定性比较对象；不生成综合资本效率分数。",
        "previous_top_candidate": prior.get("top_candidate"),
        "read_only": True,
        "safety_boundary": "资本排序不替代唯一主候选，不自动生成Trial、Confirm、金额、卖出份额或订单。",
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
