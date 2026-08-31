from __future__ import annotations

import json
import re
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
DASHBOARD = ROOT / "ETF当前状态_DASHBOARD.md"
OUT = STATE / "e2e_status.json"
POST_MARKET_REVIEW = ROOT / "post_market_review" / "post_market_review_event.json"
REVIEW_DIR = ROOT / "events" / "reviews"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


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


def latest_broker_screenshot_request() -> tuple[Path | None, dict]:
    directory = ROOT / "requests" / "live_snapshot"
    candidates = []
    for path in directory.glob("*.json"):
        request = read_json(path)
        source = str(request.get("source") or "").upper()
        scenario = str(request.get("interaction_scenario") or "").upper()
        if source != "CHATGPT_USER_BROKER_SCREENSHOT" and scenario != "BROKER_SCREENSHOT_SYNC":
            continue
        stamp = parse_time(request.get("request_time_beijing") or request.get("requested_at_beijing") or request.get("request_time"))
        if stamp:
            candidates.append((stamp, path, request))
    if not candidates:
        return None, {}
    _, path, request = max(candidates, key=lambda item: item[0])
    return path, request



def account_component(account: dict, current: dict) -> dict:
    status_raw = str(account.get("status") or "UNKNOWN").upper()
    account_time = parse_time(account.get("updated_at"))
    broker_path, broker_request = latest_broker_screenshot_request()
    request_time = parse_time(broker_request.get("request_time_beijing") or broker_request.get("requested_at_beijing") or broker_request.get("request_time"))
    ingress_pending = bool(broker_path and request_time and (account_time is None or request_time > account_time))
    audit_events = account.get("account_change_events_after_confirmed_at") or []
    pending_events = []
    for event in audit_events:
        event_time = parse_time(event.get("event_time")) if isinstance(event, dict) else None
        if account_time is not None and event_time is not None and event_time <= account_time:
            continue
        if isinstance(event, dict) and str(event.get("reconciliation_status") or "").upper().startswith("RECONCILED"):
            continue
        pending_events.append(event)

    needs_update = bool(current.get("needs_account_update")) or bool(pending_events)
    if ingress_pending:
        status = "BLOCKED"
        reason = "ACCOUNT_SYNC_NOT_PERFORMED: latest broker screenshot request is newer than canonical account_fact"
        return {
            "status": status,
            "reason": reason,
            "updated_at": account.get("updated_at"),
            "source": account.get("source"),
            "pending_change_events": pending_events,
            "historical_audit_event_count": len(audit_events),
            "pending_broker_request": {
                "path": str(broker_path.relative_to(ROOT)).replace("\\", "/"),
                "request_id": broker_request.get("request_id"),
                "request_time": request_time.isoformat(timespec="seconds"),
                "has_account_fact": isinstance(broker_request.get("account_fact"), dict),
                "status": "ACCOUNT_SYNC_NOT_PERFORMED",
            },
        }
    if status_raw == "VALID" and not needs_update:
        status = "READY"
        reason = "confirmed account facts remain valid; historical audit deltas are not pending updates"
    elif status_raw == "VALID":
        status = "DEGRADED"
        reason = "account change event exists after the latest confirmed account fact"
    else:
        status = "BLOCKED"
        reason = "confirmed account facts unavailable"
    return {
        "status": status,
        "reason": reason,
        "updated_at": account.get("updated_at"),
        "source": account.get("source"),
        "pending_change_events": pending_events,
        "historical_audit_event_count": len(audit_events),
    }


def dashboard_risk_metric(dashboard: str) -> tuple[float | None, str]:
    if not dashboard:
        return None, ""
    match = re.search(r"\|ETF策略风险率\|约?\s*([+-]?\d+(?:\.\d+)?)%", dashboard)
    if not match:
        return None, ""
    updated = re.search(r">\s*更新时间：([^\n]+)", dashboard)
    return float(match.group(1)), (updated.group(1).strip() if updated else "")


def latest_formal_review_risk() -> dict:
    directory = ROOT / "events" / "reviews"
    candidates = []
    if directory.exists():
        for path in directory.glob("*.json"):
            event = read_json(path)
            review = event.get("review") or event.get("formal_review") or {}
            fact = review.get("etf_strategy_known_net") or {}
            try:
                risk = float(fact.get("etf_strategy_risk_rate_pct"))
                equity = float(fact.get("known_net_strategy_equity"))
            except (TypeError, ValueError):
                continue
            updated = parse_time(event.get("updated_at_beijing") or event.get("account_updated_at"))
            if updated is None:
                continue
            candidates.append((updated, {"risk_pct": risk, "equity": equity, "updated_at": updated.isoformat(timespec="seconds"), "market_date": event.get("market_date") or review.get("market_date"), "source": str(path.relative_to(ROOT)).replace("\\", "/")}))
    return max(candidates, key=lambda x: x[0])[1] if candidates else {}


def risk_component(equity: dict, dashboard: str, current: dict) -> dict:
    dashboard_pct, dashboard_updated = dashboard_risk_metric(dashboard)
    summary = equity.get("summary") or {}
    reconstruction_pct = summary.get("known_net_current_strategy_return_pct")
    reconstruction_generated = equity.get("generated_at")
    reconstruction_as_of = str(equity.get("as_of_transaction_date") or "")
    market_date = str(current.get("market_date") or "")
    reconstruction_fresh_for_market = bool(
        reconstruction_as_of and market_date and reconstruction_as_of >= market_date
    )

    formal_review = latest_formal_review_risk()
    formal_review_pct = formal_review.get("risk_pct")
    if formal_review_pct is not None:
        dashboard_matches = dashboard_pct is not None and abs(float(dashboard_pct) - float(formal_review_pct)) <= 0.03
        return {
            "status": "READY",
            "reason": "latest formal post-close risk fact is authoritative; Dashboard is reconciled or automatically bypassed if transiently regressed",
            "etf_strategy_risk_pct": round(float(formal_review_pct), 2),
            "source": formal_review.get("source"),
            "source_updated_at": formal_review.get("updated_at"),
            "formal_review_market_date": formal_review.get("market_date"),
            "dashboard_risk_pct": dashboard_pct,
            "dashboard_matches_formal_review": dashboard_matches,
            "dashboard_updated_at": dashboard_updated,
            "reconstruction_risk_pct": reconstruction_pct,
            "reconstruction_generated_at": reconstruction_generated,
            "reconstruction_as_of_transaction_date": reconstruction_as_of,
            "reconstruction_fresh_for_market_date": reconstruction_fresh_for_market,
            "data_quality": "FORMAL_REVIEW_PRIMARY; DASHBOARD_RECONCILED; RECONSTRUCTION_AUXILIARY",
        }
    if dashboard_pct is not None:
        return {
            "status": "READY",
            "reason": "formal ETF strategy risk metric is available from the current Dashboard",
            "etf_strategy_risk_pct": dashboard_pct,
            "source": "ETF当前状态_DASHBOARD.md",
            "source_updated_at": dashboard_updated,
            "reconstruction_risk_pct": reconstruction_pct,
            "reconstruction_generated_at": reconstruction_generated,
            "reconstruction_as_of_transaction_date": reconstruction_as_of,
            "reconstruction_fresh_for_market_date": reconstruction_fresh_for_market,
            "data_quality": "FORMAL_DASHBOARD_CURRENT; RECONSTRUCTION_IS_AUXILIARY",
        }
    if reconstruction_pct is not None and reconstruction_fresh_for_market:
        return {
            "status": "READY",
            "reason": "formal ETF strategy risk metric is available from a current reconstruction",
            "etf_strategy_risk_pct": reconstruction_pct,
            "source": "data/state/etf_strategy_equity.json",
            "source_updated_at": reconstruction_generated,
            "reconstruction_as_of_transaction_date": reconstruction_as_of,
            "reconstruction_fresh_for_market_date": True,
            "data_quality": summary.get("known_net_equity_data_quality"),
        }
    if reconstruction_pct is not None:
        return {
            "status": "DEGRADED",
            "reason": "ETF strategy equity reconstruction is stale for the current market date and cannot be treated as the current formal risk metric",
            "etf_strategy_risk_pct": None,
            "source": "data/state/etf_strategy_equity.json",
            "stale_reconstruction_risk_pct": reconstruction_pct,
            "source_updated_at": reconstruction_generated,
            "reconstruction_as_of_transaction_date": reconstruction_as_of,
            "reconstruction_fresh_for_market_date": False,
            "data_quality": summary.get("known_net_equity_data_quality"),
        }
    return {
        "status": "BLOCKED",
        "reason": "formal ETF strategy risk metric is unavailable",
        "etf_strategy_risk_pct": None,
        "source": "",
        "data_quality": summary.get("known_net_equity_data_quality"),
    }


def close_review_component(current: dict) -> dict:
    """Require a canonical review once the formal close context is ready."""
    event = read_json(POST_MARKET_REVIEW)
    if not event or not event.get("market_close"):
        return {"status": "READY", "reason": "no formal close review is currently required"}
    market_date = str(event.get("market_date") or current.get("market_date") or "")
    review_path = REVIEW_DIR / f"{market_date}.json"
    closure_path = STATE / f"close_review_closure_{market_date}.json"
    review = read_json(review_path)
    closure = read_json(closure_path)
    complete = (
        review.get("event_type") == "FORMAL_POST_CLOSE_REVIEW"
        and isinstance(review.get("review"), dict)
        and closure.get("status") == "CLOSED"
        and closure.get("formal_review_path") == f"events/reviews/{market_date}.json"
    )
    if not complete and str(event.get("status") or "").upper() == "READY_FOR_REVIEW":
        return {
            "status": "BLOCKED",
            "reason": "FORMAL_POST_CLOSE_REVIEW_NOT_CANONICALIZED",
            "market_date": market_date,
            "review_path": f"events/reviews/{market_date}.json",
            "closure_path": f"data/state/close_review_closure_{market_date}.json",
        }
    return {"status": "READY" if complete else "DEGRADED", "reason": "canonical close review chain is complete" if complete else "close review context is not complete", "market_date": market_date}


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
    dashboard = read_text(DASHBOARD)
    components = {
        "market": market_component(data["current"]),
        "account": account_component(data["account"], data["current"]),
        "risk": risk_component(data["equity"], dashboard, data["current"]),
        "close_review": close_review_component(data["current"]),
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
        "schema_version": "1.1",
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

