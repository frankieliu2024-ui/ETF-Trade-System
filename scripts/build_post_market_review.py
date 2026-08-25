from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current
    from market_quote_router import build_market_quote_context
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current
    from scripts.market_quote_router import build_market_quote_context

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CLOSE_NODES = {"1500", "close"}
CLOSE_PHASES = {"POST_CLOSE_GRACE", "CLOSED"}


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
    node = current.get("latest_valid_node", "")
    data_status = current.get("data_freshness", {})
    market_phase = data_status.get("market_phase", "")
    # Production query-time/close-grace snapshots may retain node="live" after
    # 15:00. Treat an explicit post-close phase as market close; otherwise a
    # valid 15:00/close node remains sufficient.
    market_close = node in CLOSE_NODES or market_phase in CLOSE_PHASES
    data_complete = current.get("node_status") == "READY" and data_status.get("status") not in {"DATA_ERROR", "DEGRADED", "STALE", "FAILED"}
    waiting = account["status"] != "VALID"
    if market_close and waiting:
        status = "WAITING_USER"
    elif market_close and data_complete:
        status = "READY_FOR_REVIEW"
    else:
        status = "BLOCKED"

    # The formal review event is written by the authenticated state-sync path.
    # Reflect its real existence instead of leaving a permanently false flag.
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
        "market_data_complete": data_complete,
        "need_account_screenshot": waiting,
        "account_fact_status": account["status"],
        "account_updated_at": account.get("updated_at", ""),
        "status": status,
        "same_day_review_idempotent": True,
        "generated_at": now_utc(),
        "context_file": "data/state/review_context.json",
        "market_quote_router": build_market_quote_context(root),
        "prohibited_outputs": ["trade_amount", "buy_action", "sell_action", "order"],
    }
    review = {
        "generated_at": now_utc(),
        "market_date": current.get("market_date", ""),
        "review_key": current.get("market_date", ""),
        "market_close": market_close,
        "market_phase": market_phase,
        "daily_market_summary": {
            "latest_valid_node": node,
            "data_status": data_status,
            "provider": data_status.get("provider", ""),
            "latest_snapshot": current.get("latest_snapshot", ""),
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
        "review_boundary": "15:00后当天首次最终账户截图默认触发正式收盘复盘；账户事实只能来自用户或券商确认。重复截图如无账户/成交变化只做差异更新，不重复制造CASE。A股复盘使用15:00正式收盘数据，不用截图提交时间冒充行情时间。",
    }
    return {"event": event, "review": review}


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "review_context.json", result["review"])
    if result["event"]["market_close"]:
        atomic_json_write(ROOT / "post_market_review" / "post_market_review_event.json", result["event"])
    print(json.dumps({"ok": True, "market_close": result["event"]["market_close"], "status": result["event"]["status"], "trade_output_generated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
