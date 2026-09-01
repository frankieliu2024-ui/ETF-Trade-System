from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current
    from rules_version import current_rule_version
    from market_quote_router import build_market_quote_context
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current
    from scripts.rules_version import current_rule_version
    from scripts.market_quote_router import build_market_quote_context

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CLOSE_NODES = {"1500", "close"}
CLOSE_PHASES = {"POST_CLOSE_GRACE", "CLOSED", "OUTSIDE_SESSION"}


def load_snapshot(root: Path, current: dict) -> dict:
    rel = str(current.get("latest_snapshot") or "")
    if not rel:
        return {}
    path = root / rel
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_close_data_contract(root: Path, current: dict) -> dict:
    """Separate A-share session-close effective time from provider observation time.

    A provider may answer a close query after 15:00 and stamp the response with its
    observation/query time. That timestamp must be preserved, but it is not the
    economic effective time of an A-share close price. An explicit close node with
    complete same-day OHLC/volume facts is therefore represented as the 15:00
    session close plus a later provider-observation range.
    """
    snapshot = load_snapshot(root, current)
    market_date = str(current.get("market_date") or snapshot.get("market_date") or "")
    rows = snapshot.get("rows") or []
    complete_rows = bool(rows) and all(
        str(row.get("quality_status") or "").upper() == "PASS"
        and all(row.get(field) is not None for field in ("open", "high", "low", "close", "volume", "amount"))
        for row in rows
    )
    explicit_close_node = (
        str(current.get("latest_valid_node") or "") in CLOSE_NODES
        and str(snapshot.get("node") or "") in CLOSE_NODES
        and str(snapshot.get("planned_time") or "") == "15:00"
    )
    same_market_date = bool(market_date) and str(snapshot.get("market_date") or "") == market_date
    snapshot_pass = str(snapshot.get("quality_status") or "").upper() == "PASS"
    verified = bool(explicit_close_node and same_market_date and snapshot_pass and complete_rows)
    observation_times = sorted(
        str(row.get("as_of_beijing"))
        for row in rows
        if row.get("as_of_beijing")
    )
    return {
        "schema_version": "1.1",
        "status": "VERIFIED_SESSION_CLOSE" if verified else "UNVERIFIED",
        "verified_session_close": verified,
        "market_date": market_date,
        "effective_market_time_beijing": f"{market_date}T15:00:00+08:00" if verified else "",
        "provider_observation_time_min_beijing": observation_times[0] if observation_times else "",
        "provider_observation_time_max_beijing": observation_times[-1] if observation_times else "",
        "snapshot_captured_at_beijing": snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "",
        "latest_snapshot": current.get("latest_snapshot", ""),
        "snapshot_node": snapshot.get("node", ""),
        "snapshot_planned_time": snapshot.get("planned_time", ""),
        "snapshot_market_phase": snapshot.get("market_phase", ""),
        "row_count": len(rows),
        "complete_rows": complete_rows,
        "rule": "A股显式close节点的15:00完整同日收盘行情，以15:00为价格有效时点；provider在15:00后的时间戳仅表示查询/观察时点，必须保留但不得据此把收盘价误判为15:15后的成交价。若不是显式close节点或字段/质量不完整，则不得套用该语义。",
    }


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
    node = current.get("latest_valid_node", "")
    data_status = current.get("data_freshness", {})
    market_phase = data_status.get("market_phase", "")
    close_contract = build_close_data_contract(root, current)

    # A verified explicit close node is authoritative even when the provider
    # observation/query timestamp is later than 15:00. For non-close nodes we
    # retain the existing phase-based post-close compatibility path.
    market_close = bool(close_contract.get("verified_session_close")) or (
        node not in CLOSE_NODES and market_phase in {"POST_CLOSE_GRACE", "CLOSED"}
    )
    data_complete = current.get("node_status") == "READY" and data_status.get("status") not in {"DATA_ERROR", "DEGRADED", "STALE", "FAILED"}
    waiting = account["status"] != "VALID"
    if market_close and waiting:
        status = "WAITING_USER"
    elif market_close and data_complete:
        status = "READY_FOR_REVIEW"
    else:
        status = "BLOCKED"

    review_event_path = root / "events" / "reviews" / f"{current.get('market_date', '')}.json"
    formal_review_generated = False
    if review_event_path.exists():
        try:
            prior_event = json.loads(review_event_path.read_text(encoding="utf-8"))
            formal_review_generated = (
                prior_event.get("event_type") == "FORMAL_POST_CLOSE_REVIEW"
                and prior_event.get("market_date") == current.get("market_date")
                and isinstance(prior_event.get("review"), dict)
            )
        except (OSError, json.JSONDecodeError):
            formal_review_generated = False

    event = {
        "event_type": "POST_MARKET_REVIEW_REQUIRED",
        "market_date": current.get("market_date", ""),
        "review_key": current.get("market_date", ""),
        "market_close": market_close,
        "latest_valid_node": node,
        "market_phase": market_phase,
        "close_data_contract": close_contract,
        "market_data_complete": data_complete,
        "need_account_screenshot": waiting,
        "account_fact_status": account["status"],
        "account_updated_at": account.get("updated_at", ""),
        "status": status,
        "same_day_review_idempotent": True,
        "generated_at": now_utc(),
        "rules_version": current_rule_version(root) or decision.get("rules_version", ""),
        "context_file": "data/state/review_context.json",
        "market_quote_router": build_market_quote_context(root),
        "prohibited_outputs": ["trade_amount", "buy_action", "sell_action", "order"],
    }
    review = {
        "generated_at": now_utc(),
        "rules_version": current_rule_version(root) or decision.get("rules_version", ""),
        "market_date": current.get("market_date", ""),
        "review_key": current.get("market_date", ""),
        "market_close": market_close,
        "market_phase": market_phase,
        "close_data_contract": close_contract,
        "daily_market_summary": {
            "latest_valid_node": node,
            "data_status": data_status,
            "provider": data_status.get("provider", ""),
            "latest_snapshot": current.get("latest_snapshot", ""),
            "effective_market_time_beijing": close_contract.get("effective_market_time_beijing", ""),
            "provider_observation_time_max_beijing": close_contract.get("provider_observation_time_max_beijing", ""),
        },
        "current": current,
        "market_quote_router": build_market_quote_context(root),
        "dashboard_summary": decision.get("dashboard_summary", {}),
        "account_fact_status": account["status"],
        "account_updated_at": account.get("updated_at", ""),
        "need_account_screenshot": waiting,
        "review_status": status,
        "waiting_for_user_screenshot": waiting,
        "formal_review_generated": formal_review_generated,
        "formal_review_allowed": bool(market_close and data_complete and not waiting),
        "same_day_review_idempotent": True,
        "review_boundary": "15:00后当天首次最终账户截图默认触发正式收盘复盘。A股显式close节点必须把15:00价格有效时点与provider后续查询/观察时间分离；provider时间晚于15:00不等于发生了盘后成交。账户事实只能来自用户或券商确认；重复截图如无账户/成交变化只做差异更新，不重复制造CASE。",
    }
    return {"event": event, "review": review}


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "review_context.json", result["review"])
    if result["event"]["market_close"]:
        atomic_json_write(ROOT / "post_market_review" / "post_market_review_event.json", result["event"])
    print(json.dumps({"ok": True, "market_close": result["event"]["market_close"], "status": result["event"]["status"], "close_data_status": result["event"]["close_data_contract"]["status"], "trade_output_generated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
