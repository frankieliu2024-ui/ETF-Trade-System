from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from state_manager import atomic_json_write, now_utc, read_account_fact, read_current


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def main() -> None:
    current = read_current(ROOT)
    account = read_account_fact(ROOT)
    created_at = now_utc()
    node = current.get("latest_valid_node", "")
    market_date = current.get("market_date", "")
    basis = json.dumps({"market_date": market_date, "node": node, "captured_at": current.get("captured_at", "")}, ensure_ascii=False, sort_keys=True)
    notification_id = "notification_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
    data_status = current.get("data_freshness", {}).get("status", current.get("node_status", ""))
    need_user_action = account["status"] != "VALID"
    event = {
        "notification_id": notification_id,
        "created_at": created_at,
        "market_date": market_date,
        "node": node,
        "latest_valid_node": node,
        "title": "ETF节点提醒",
        "data_status": data_status,
        "account_fact_status": account["status"],
        "need_user_action": need_user_action,
        "reason": "账户事实缺失，请上传最新券商截图" if need_user_action else "状态提醒",
        "context_file": "decision_context.json",
        "current_version": current.get("rules_version", "V2.2.15"),
        "body": f"【ETF节点提醒】\n时间：{current.get('captured_at', '')}\n最新有效节点：{node}\n数据状态：{data_status}\n账户状态：{account['status']}\n是否需要处理：{'需要上传账户截图' if need_user_action else '无需补充账户事实'}",
        "status": "CREATED",
    }
    atomic_json_write(ROOT / "notifications" / "notification_event.json", event)
    print(json.dumps({"ok": True, "notification_id": notification_id, "node": node, "account_fact_status": account["status"], "trade_output_generated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

