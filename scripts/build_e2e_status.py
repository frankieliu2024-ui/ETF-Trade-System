from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "state"
TZ = timezone(timedelta(hours=8))

FILES = {
    "current": STATE / "CURRENT.json",
    "account": STATE / "account_fact.json",
    "equity": STATE / "etf_strategy_equity.json",
    "query": STATE / "query_context.json",
    "decision": STATE / "decision_context.json",
    "maintenance": STATE / "maintenance_health.json",
}
OUT = STATE / "e2e_status.json"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def market_component(current: dict) -> dict:
    freshness = current.get("data_freshness") or {}
    node = str(current.get("node_status") or "UNKNOWN").upper()
    fresh = str(freshness.get("status") or "UNKNOWN").upper()
    if node == "READY" and fresh == "PASS":
        status = "READY"
        reason = "latest market snapshot is valid"
    elif current.get("latest_snapshot") and fresh in {"DEGRADED", "PASS"}:
        status = "DEGRADED"
        reason = f"market data quality={fresh}, node={node}"
    else:
        status = "BLOCKED"
        reason = "no usable latest market snapshot"
    return {
        "status": status,
        "reason": reason,
        "snapshot": current.get("latest_snapshot"),
        "captured_at": current.get("captured_at"),
        "market_phase": freshness.get("market_phase"),
    }


def account_component(account: dict, current: dict) -> dict:
    status_raw = str(account.get("status") or "UNKNOWN").upper()
    changed = account.get("account_change_events_after_confirmed_at") or []
    needs_update = bool(current.get("needs_account_update")) or bool(changed)
    if status_raw == "VALID" and not needs_update:
        status = "READY"
        reason = "confirmed account facts remain valid under event-driven carry-forward"
    elif status_raw == "VALID":
        status = "DEGRADED"
        reason = "account change event exists after last confirmed account fact"
    else:
        status = "BLOCKED"
        reason = "confirmed account facts unavailable"
    return {
        "status": status,
        "reason": reason,
        "updated_at": account.get("updated_at"),
        "source": account.get("source"),
        "pending_change_events": changed,
    }


def risk_component(equity: dict) -> dict:
    summary = equity.get("summary") or {}
    risk_pct = summary.get("known_net_current_strategy_return_pct")
    data_quality = summary.get("known_net_equity_data_quality")
    if risk_pct is not None:
        status = "READY"
        reason = "formal ETF strategy risk metric is available"
    else:
        status = "BLOCKED"
        reason = "formal ETF strategy risk metric is unavailable"
    return {
        "status": status,
        "reason": reason,
        "etf_strategy_risk_pct": risk_pct,
        "data_quality": data_quality,
    }


def context_component(query: dict, decision: dict) -> dict:
    query_ok = bool(query)
    decision_ok = bool(decision)
    if query_ok and decision_ok:
        status = "READY"
        reason = "query and decision contexts are available"
    elif query_ok or decision_ok:
        status = "DEGRADED"
        reason = "one derived decision context is missing"
    else:
        status = "BLOCKED"
        reason = "query and decision contexts are unavailable"
    return {"status": status, "reason": reason, "query_context": query_ok, "decision_context": decision_ok}


def maintenance_component(maintenance: dict) -> dict:
    raw = str(maintenance.get("status") or "UNKNOWN").upper()
    if raw == "PASS":
        status = "READY"
        reason = "maintenance guard passed"
    elif raw in {"WARN", "DEGRADED"}:
        status = "DEGRADED"
        reason = f"maintenance status={raw}"
    else:
        status = "BLOCKED"
        reason = f"maintenance status={raw}"
    return {"status": status, "reason": reason, "checked_at": maintenance.get("checked_at")}


def main() -> int:
    data = {name: read_json(path) for name, path in FILES.items()}
    components = {
        "market": market_component(data["current"]),
        "account": account_component(data["account"], data["current"]),
        "risk": risk_component(data["equity"]),
        "decision_context": context_component(data["query"], data["decision"]),
        "maintenance": maintenance_component(data["maintenance"]),
    }

    statuses = [x["status"] for x in components.values()]
    if "BLOCKED" in statuses:
        overall = "BLOCKED"
    elif "DEGRADED" in statuses:
        overall = "DEGRADED"
    else:
        overall = "READY"

    blockers = [name for name, value in components.items() if value["status"] == "BLOCKED"]
    degradations = [name for name, value in components.items() if value["status"] == "DEGRADED"]

    result = {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "status": overall,
        "purpose": "TOP_LEVEL_SYSTEM_USABILITY_ONLY_NOT_A_TRADING_PERMISSION",
        "decision_rule": "READY=all critical components usable; DEGRADED=analysis possible with explicit caveat; BLOCKED=missing critical fact prevents formal amount/share decision",
        "components": components,
        "blockers": blockers,
        "degradations": degradations,
        "safety_boundary": "This state does not create or modify risk permission, Trial/Confirm rules, amounts, sell rules, or execution authority."
    }
    write_json(OUT, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
