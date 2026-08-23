from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CLOSE_NODES = {"1500", "close"}


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
    node = current.get("latest_valid_node", "")
    market_close = node in CLOSE_NODES
    data_status = current.get("data_freshness", {})
    data_complete = current.get("node_status") == "READY" and data_status.get("status") not in {"DATA_ERROR", "DEGRADED"}
    waiting = account["status"] != "VALID"
    event = {
        "event_type": "POST_MARKET_REVIEW_REQUIRED", "market_date": current.get("market_date", ""),
        "market_close": market_close, "latest_valid_node": node, "market_data_complete": data_complete,
        "need_account_screenshot": waiting, "account_fact_status": account["status"],
        "status": "WAITING_USER" if market_close and waiting else ("READY_FOR_REVIEW" if market_close and data_complete else "BLOCKED"),
        "generated_at": now_utc(), "context_file": "review_context.json",
        "prohibited_outputs": ["trade_amount", "buy_action", "sell_action", "order"],
    }
    review = {
        "generated_at": now_utc(), "market_date": current.get("market_date", ""), "market_close": market_close,
        "daily_market_summary": {"latest_valid_node": node, "data_status": data_status, "provider": data_status.get("provider", ""), "latest_snapshot": current.get("latest_snapshot", "")},
        "current": current, "dashboard_summary": decision.get("dashboard_summary", {}),
        "account_fact_status": account["status"], "need_account_screenshot": waiting,
        "review_status": event["status"], "waiting_for_user_screenshot": waiting,
        "formal_review_generated": False, "formal_review_allowed": bool(market_close and data_complete and not waiting),
        "review_boundary": "账户事实只能来自用户上传；账户缺失时不生成正式复盘结论。",
    }
    return {"event": event, "review": review}


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "review_context.json", result["review"])
    if result["event"]["market_close"]:
        atomic_json_write(ROOT / "post_market_review" / "post_market_review_event.json", result["event"])
    print(json.dumps({"ok": True, "market_close": result["event"]["market_close"], "status": result["event"]["status"], "trade_output_generated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

