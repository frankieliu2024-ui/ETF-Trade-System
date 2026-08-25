from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
NOTIFICATIONS = STATE / "notification_center.json"
OUTCOME_DIR = ROOT / "events" / "research" / "decision_outcomes"
REQUEST_DIR = ROOT / "requests" / "state_sync"
TZ = timezone(timedelta(hours=8))


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def clean_answer(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return {"确认": "CONFIRM", "是": "CONFIRM", "否": "DECLINE", "拒绝": "DECLINE", "补充成交明细": "DETAILS"}.get(raw, raw)


def find_notification(items: list[dict], notification_id: str) -> dict | None:
    for item in items:
        if str(item.get("notification_id") or "") == notification_id:
            return item
    return None


def update_state(items: list[dict], last: dict) -> None:
    waiting = [str(x.get("notification_id")) for x in items if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]
    recent = [{
        "key": x.get("source_event_id"),
        "type": x.get("event_type"),
        "title": x.get("title"),
        "content": x.get("content"),
        "source": x.get("source"),
        "status": x.get("lifecycle_status"),
        "attempted_at": x.get("last_attempted_at") or x.get("sent_at") or x.get("created_at"),
        "response": x.get("response") or {},
        "notification_id": x.get("notification_id"),
        "lifecycle_status": x.get("lifecycle_status"),
    } for x in items[-50:]]
    write(NOTIFICATIONS, {
        "schema_version": "2.0",
        "updated_at": now(),
        "last_status": last.get("lifecycle_status"),
        "last_type": last.get("event_type"),
        "last_title": last.get("title"),
        "notifications": items[-50:],
        "recent": recent,
        "pending_questions": waiting,
        "policy": "只推送需要用户关注、确认或决策的事项；普通行情刷新、成功自愈和普通后台运行不推送。",
        "safety_boundary": "确认入口不猜测成交，不从最终持仓反推历史交易；只有用户明确确认后才进入trade_event。",
    })


def main() -> int:
    parser = argparse.ArgumentParser(description="Confirm a delayed execution candidate without inferring an unconfirmed trade.")
    parser.add_argument("request_path", help="JSON request under requests/ or a local path inside repository")
    args = parser.parse_args()
    request_path = (ROOT / args.request_path).resolve()
    if ROOT not in request_path.parents or not request_path.exists():
        raise RuntimeError("invalid confirmation request path")
    request = read(request_path, {})
    notification_id = str(request.get("notification_id") or "")
    answer = clean_answer(request.get("answer"))
    state = read(NOTIFICATIONS, {})
    items = list(state.get("notifications") or [])
    item = find_notification(items, notification_id)
    if item is None:
        raise RuntimeError("notification_id not found")
    if item.get("lifecycle_status") in {"CONFIRMED", "ARCHIVED"}:
        print(json.dumps({"status": "ALREADY_CONFIRMED", "notification_id": notification_id}, ensure_ascii=False))
        return 0
    if answer == "DECLINE":
        item["lifecycle_status"] = "ARCHIVED"
        item["archived_at"] = now()
        item["confirmation_answer"] = "DECLINE"
        update_state(items, item)
        print(json.dumps({"status": "DECLINED_NO_TRADE", "notification_id": notification_id}, ensure_ascii=False))
        return 0
    if answer == "DETAILS":
        item["lifecycle_status"] = "WAITING_CONFIRMATION"
        item["pending_question"] = "请补充实际交易日、方向、数量和成交价/成交金额；在补充前不会生成正式成交事件。"
        item["last_question_at"] = now()
        update_state(items, item)
        print(json.dumps({"status": "DETAILS_REQUIRED", "notification_id": notification_id, "question": item["pending_question"]}, ensure_ascii=False))
        return 0
    if answer != "CONFIRM":
        raise RuntimeError("answer must be 确认/否/补充成交明细")

    context = item.get("confirmation_context") or {}
    supplied = request.get("trade") if isinstance(request.get("trade"), dict) else {}
    code = str(supplied.get("code") or context.get("security_code") or item.get("security_code") or "")
    name = str(supplied.get("name") or context.get("security_name") or item.get("security_name") or code)
    side = str(supplied.get("side") or context.get("side") or "")
    quantity = supplied.get("quantity", context.get("quantity"))
    if not code or not side or quantity in (None, "", 0):
        item["lifecycle_status"] = "WAITING_CONFIRMATION"
        item["pending_question"] = "请补充实际交易方向和成交数量；确认匹配不等于系统可以猜测成交明细。"
        item["last_question_at"] = now()
        update_state(items, item)
        print(json.dumps({"status": "DETAILS_REQUIRED", "notification_id": notification_id, "question": item["pending_question"]}, ensure_ascii=False))
        return 0

    confirmation_date = now()
    execution_date = str(supplied.get("execution_date") or context.get("suggested_execution_date") or confirmation_date[:10])
    trade = {
        "event_id": "trade_confirmed_" + hashlib.sha256(f"{notification_id}|{execution_date}|{code}|{side}|{quantity}".encode("utf-8")).hexdigest()[:20],
        "code": code,
        "name": name,
        "side": side,
        "quantity": quantity,
        "price": supplied.get("price"),
        "amount": supplied.get("amount", context.get("approx_amount_yuan")),
        "lifecycle": supplied.get("lifecycle") or context.get("lifecycle"),
        "decision_id": supplied.get("decision_id") or context.get("decision_id") or None,
        "hypothesis_id": supplied.get("hypothesis_id"),
        "execution_date": execution_date,
        "confirmed_at_beijing": confirmation_date,
        "confirmation_date": confirmation_date[:10],
        "source": "USER_CONFIRMED_NOTIFICATION",
        "source_confidence": "USER_EXPLICIT_CONFIRMATION",
        "notification_id": notification_id,
    }
    request_payload = {
        "request_id": "state_sync_" + trade["event_id"],
        "source": "USER_CONFIRMED_NOTIFICATION",
        "interaction_scenario": "USER_CONFIRMED_DELAYED_EXECUTION",
        "market_date": execution_date,
        "trade_event": trade,
        "confirmation_date": confirmation_date[:10],
        "execution_date": execution_date,
        "notification_id": notification_id,
    }
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    generated_path = REQUEST_DIR / f"{request_payload['request_id']}.json"
    write(generated_path, request_payload)
    result = subprocess.run(["python", str(ROOT / "scripts" / "process_state_sync_request.py"), str(generated_path.relative_to(ROOT))], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        item["lifecycle_status"] = "WAITING_CONFIRMATION"
        item["last_error"] = result.stderr[-500:] or result.stdout[-500:]
        update_state(items, item)
        raise RuntimeError("state sync failed after explicit confirmation")
    item["lifecycle_status"] = "CONFIRMED"
    item["confirmed_at"] = confirmation_date
    item["confirmation_answer"] = "CONFIRM"
    item["execution_fact_ref"] = f"events/trades/{trade['event_id']}.json"
    item["execution_date"] = execution_date
    item["confirmation_date"] = confirmation_date[:10]
    item["pending_question"] = None
    update_state(items, item)

    OUTCOME_DIR.mkdir(parents=True, exist_ok=True)
    outcome_path = OUTCOME_DIR / f"TRADE_COMPLETED_REVIEW_REQUIRED_{trade['event_id']}.json"
    write(outcome_path, {
        "event_type": "TRADE_COMPLETED_REVIEW_REQUIRED",
        "event_id": trade["event_id"],
        "notification_id": notification_id,
        "related_decision_id": trade.get("decision_id"),
        "execution_date": execution_date,
        "confirmation_date": confirmation_date[:10],
        "security_code": code,
        "security_name": name,
        "side": side,
        "quantity": quantity,
        "price": trade.get("price"),
        "amount": trade.get("amount"),
        "review_status": "PENDING_FORMAL_REVIEW",
        "objective_fields": ["trade_event", "strategy_risk_rate_change", "cash_change", "next_observation_point"],
        "safety_boundary": "只生成客观复盘待办，不自动判断交易正确/错误，不修改MASTER，不生成新的交易动作。",
    })
    print(json.dumps({"status": "CONFIRMED_AND_PROPAGATED", "notification_id": notification_id, "trade_event_id": trade["event_id"], "execution_date": execution_date, "confirmation_date": confirmation_date[:10], "state_sync": result.stdout.strip()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
