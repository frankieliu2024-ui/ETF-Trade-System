from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
TZ = timezone(timedelta(hours=8))
PUSHPLUS_URL = "https://www.pushplus.plus/send"
HISTORY_LIMIT = 50


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


def send(token: str, title: str, content: str) -> tuple[bool, dict]:
    payload = json.dumps({"token": token, "title": title, "content": content, "template": "markdown", "channel": "wechat"}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(PUSHPLUS_URL, data=payload, headers={"Content-Type": "application/json", "User-Agent": "ETF-Trade-System/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"raw": raw[:500]}
            ok = int(body.get("code", 0) or 0) == 200
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


def decision_event() -> dict | None:
    trigger = read_json(STATE / "decision_trigger.json", {})
    if not trigger.get("requires_formal_reassessment") or str(trigger.get("status") or "") not in {"TRIGGERED", "ALREADY_RECORDED"}:
        return None
    key = str(trigger.get("idempotency_key") or "")
    if not key:
        return None
    account = read_json(STATE / "account_fact.json", {})
    event_type, applicable = str(trigger.get("trigger_type") or ""), str(trigger.get("applicable_object") or "")
    if event_type == "TRADE_CONFIRMED":
        target = trade_display(applicable); title = f"{target}成交后需要重新评估"; content = f"{target}的成交已经确认，账户持仓或现金结构发生变化。\n\n建议：现在重新检查持仓、资金和下一步交易安排。"
    elif event_type == "ACCOUNT_STRUCTURE_CHANGED":
        target = display_from_code(applicable, account); title = "账户状态发生重要变化"; content = f"{target}相关的持仓、现金或资产结构出现已确认变化。\n\n建议：现在重新检查当前持仓和可用资金，确认是否需要调整原交易判断。"
    elif event_type == "RISK_BOUNDARY_CROSSED":
        title = "ETF策略风险状态发生变化"; content = "ETF策略风险状态已跨过关键区间，这可能改变新增交易的可用空间。\n\n建议：现在重新评估风险许可和当前交易计划。"
    elif event_type == "E2E_RECOVERED":
        title = "此前暂缓的交易判断现在可以继续"; content = "此前因为关键行情、账户或决策信息不完整而暂缓的交易判断，现在所需信息已经补齐。\n\n建议：重新处理之前尚未完成的交易判断。"
    else:
        target = display_from_code(applicable, account); title = f"{target}出现值得重新评估的新变化"; content = f"{target}出现了可能改变原交易判断的新市场或研究证据。\n\n建议：现在重新检查该标的的机会、持仓或风险收益判断。"
    return {"key": f"decision:{key}", "type": "交易判断", "title": title, "content": content, "source": "decision_trigger", "user_severity": "需要关注", "user_action": "重新评估交易计划"}


def account_confirmation_event() -> dict | None:
    account = read_json(STATE / "account_fact.json", {})
    unresolved = [e for e in (account.get("account_change_events_after_confirmed_at") or []) if str(e.get("reconciliation_status") or "").upper() == "UNRECONCILED_ACCOUNT_CHANGE"]
    if not unresolved:
        return None
    event = unresolved[-1]
    code = str(event.get("code") or event.get("object") or "")
    target = display_from_code(code, account)
    event_time = str(event.get("event_time") or event.get("occurred_at") or account.get("updated_at") or "")
    return {"key": f"account:{code}:{event_time}:{event.get('change_summary','')}", "type": "账户确认", "title": "发现一项需要你确认的账户变化", "content": f"{target}相关的账户变化暂时无法由已确认成交或其他已知事件解释。\n\n建议：请确认是否存在未记录成交、资金划转或其他账户变化；必要时上传当前账户截图。", "source": "account_fact", "user_severity": "需要操作", "user_action": "确认账户变化或上传截图"}


def failed_steps_text(diag: dict) -> str:
    parts = []
    for row in diag.get("failed_steps") or []:
        step = str(row.get("step") or "").strip()
        job = str(row.get("job") or "").strip()
        if step:
            parts.append(f"{job + ' / ' if job else ''}{step}")
    return "；".join(parts[:3]) or "未提供具体失败步骤"


def system_event() -> dict | None:
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
    # Historical/stale failures must not be pushed as current incidents. A newer main has already superseded them.
    if (head_sha and main_head_sha and head_sha != main_head_sha) or not head_is_current_main:
        return None

    workflow = str(diag.get("workflow_name") or "后台任务")
    run_id = str(diag.get("run_id") or "未知")
    classification = str(diag.get("classification") or "未知")
    steps = failed_steps_text(diag)
    changed_files = [str(x) for x in (diag.get("changed_files") or []) if x]
    changed_text = "、".join(changed_files[:3]) if changed_files else "无明确业务数据文件变更"

    if workflow == "ETF market snapshot":
        severity = "影响交易判断"
        action_text = "暂缓依据系统做新的交易判断，等待行情采集恢复。"
        impact = "行情采集任务失败，最新ETF/指数行情可能不完整；账户事实不会因此被自动修改。"
    elif workflow == "ETF system consistency":
        severity = "需要关注"
        action_text = "暂时无需手工修复；如果你正准备依赖系统做交易判断，请先等待下一轮一致性检查结果。"
        impact = "系统一致性检查失败，说明某项状态或校验未通过；不代表券商账户或实际持仓发生变化。"
    else:
        severity = "需要关注"
        action_text = "暂时无需手工修改数据；如后续影响行情、账户或交易判断，系统会升级通知。"
        impact = "后台维护任务失败，但当前诊断没有证据表明券商账户或持仓被改动。"

    title = f"【真实运行异常｜{severity}】{workflow}失败"
    content = (
        f"发生了什么：{workflow}运行失败（Run {run_id}）。\n\n"
        f"具体失败：{steps}。\n\n"
        f"影响：{impact}\n\n"
        f"涉及变更：{changed_text}。\n\n"
        f"你现在需要做什么：{action_text}\n\n"
        f"系统为什么没有自动修：自动处理触及安全边界，因此停止自动修改。诊断分类：{classification}。"
    )
    return {"key": f"system:workflow:{run_id}:{classification}", "type": "系统异常", "title": title, "content": content, "source": "workflow_failure_diagnostic", "user_severity": severity, "user_action": action_text}


def close_account_event(force: bool = False) -> dict | None:
    current, account = read_json(STATE / "CURRENT.json", {}), read_json(STATE / "account_fact.json", {})
    market_date = str(current.get("market_date") or "")
    if not market_date:
        return None
    dt = now()
    if not force and (dt.weekday() >= 5 or dt.hour < 15):
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
    return {"key": f"close-account:{market_date}", "type": "收盘账户", "title": "今日收盘账户信息尚未确认", "content": "系统还没有今天15:00之后的最终账户信息。\n\n建议：请上传收盘持仓截图；如果今天15:00后账户没有任何变化，也可以直接确认“收盘账户无变化”。", "source": "account_fact", "user_severity": "需要操作", "user_action": "上传收盘截图或确认收盘账户无变化"}


def choose_event(mode: str) -> dict | None:
    if mode == "close": return close_account_event()
    if mode == "close-test": return close_account_event(force=True)
    for builder in (execution_confirmation_event, account_confirmation_event, system_event, decision_event):
        event = builder()
        if event: return event
    return None


LIFECYCLE_STATUSES = {"CREATED", "SENT", "WAITING_CONFIRMATION", "CONFIRMED", "ARCHIVED", "EXPIRED"}
USER_ACTION_TYPES = {"成交确认", "账户确认", "收盘账户", "交易判断", "系统异常"}
NOTIFICATION_TTL_DAYS = 2


def notification_id_for(event: dict) -> str:
    source = str(event.get("source_event_id") or event.get("key") or "")
    return "notification_" + __import__("hashlib").sha256(source.encode("utf-8")).hexdigest()[:20]


def normalize_notification(event: dict, record: dict | None = None) -> dict:
    record = record or {}
    created = str(record.get("created_at") or event.get("created_at") or now().isoformat(timespec="seconds"))
    context = event.get("confirmation_context") or {}
    return {
        "notification_id": str(record.get("notification_id") or event.get("notification_id") or notification_id_for(event)),
        "event_type": str(record.get("event_type") or event.get("event_type") or event.get("type") or "SYSTEM_EVENT"),
        "source_event_id": str(record.get("source_event_id") or event.get("source_event_id") or event.get("key") or ""),
        "related_decision_id": str(record.get("related_decision_id") or event.get("related_decision_id") or context.get("decision_id") or ""),
        "security_code": str(record.get("security_code") or event.get("security_code") or context.get("security_code") or ""),
        "security_name": str(record.get("security_name") or event.get("security_name") or context.get("security_name") or ""),
        "user_severity": str(record.get("user_severity") or event.get("user_severity") or ""),
        "user_action": str(record.get("user_action") or event.get("user_action") or ""),
        "lifecycle_status": str(record.get("lifecycle_status") or record.get("status") or "CREATED"),
        "created_at": created,
        "sent_at": record.get("sent_at"),
        "confirmed_at": record.get("confirmed_at"),
        "archived_at": record.get("archived_at"),
        "expires_at": record.get("expires_at") or ((parse_notification_time(created) + timedelta(days=NOTIFICATION_TTL_DAYS)).isoformat(timespec="seconds") if parse_notification_time(created) else None),
        "title": str(record.get("title") or event.get("title") or ""),
        "content": str(record.get("content") or event.get("content") or ""),
        "source": str(record.get("source") or event.get("source") or ""),
        "confirmation_context": context or record.get("confirmation_context") or {},
        "response": record.get("response") or {},
        "last_attempted_at": record.get("last_attempted_at") or record.get("attempted_at"),
    }


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


def expire_notifications(items: list[dict]) -> list[dict]:
    current = now()
    out = []
    for raw in items:
        item = normalize_notification(raw, raw)
        status = str(item.get("lifecycle_status") or "")
        expiry = parse_notification_time(item.get("expires_at"))
        if status in {"SENT", "WAITING_CONFIRMATION"} and expiry and current >= expiry:
            item["lifecycle_status"] = "EXPIRED"
            item["archived_at"] = item.get("archived_at") or current.isoformat(timespec="seconds")
        out.append(item)
    return out


def find_existing_notification(items: list[dict], event: dict) -> dict | None:
    source = str(event.get("source_event_id") or event.get("key") or "")
    nid = str(event.get("notification_id") or "")
    for item in items:
        if nid and str(item.get("notification_id") or "") == nid:
            return item
        if source and str(item.get("source_event_id") or "") == source:
            return item
    return None


def compact_recent(item: dict) -> dict:
    return {
        "key": item.get("source_event_id"),
        "type": item.get("event_type"),
        "title": item.get("title"),
        "content": item.get("content"),
        "source": item.get("source"),
        "user_severity": item.get("user_severity"),
        "user_action": item.get("user_action"),
        "status": "SENT" if item.get("lifecycle_status") in {"SENT", "WAITING_CONFIRMATION"} else item.get("lifecycle_status"),
        "attempted_at": item.get("last_attempted_at") or item.get("sent_at") or item.get("created_at"),
        "response": item.get("response") or {},
        "notification_id": item.get("notification_id"),
        "lifecycle_status": item.get("lifecycle_status"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["event", "close", "close-test", "channel-test"], default="event")
    args = parser.parse_args()
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    state_path = STATE / "notification_center.json"
    state = read_json(state_path, {"schema_version": "1.0", "recent": []})
    raw_items = list(state.get("notifications") or [])
    if not raw_items:
        raw_items = [normalize_notification(x, x) for x in (state.get("recent") or [])]
    notifications = expire_notifications(raw_items)

    if args.mode == "channel-test":
        event = {"key": f"channel-test:{now().isoformat(timespec='seconds')}", "type": "测试", "title": "【测试】ETF系统通知中心", "content": "这是一条通知通道测试，不代表真实行情、账户、交易或系统故障。\n\n你现在需要做什么：无需操作。收到即表示 GitHub → PushPlus → 微信通道正常。", "source": "manual_test", "event_type": "CHANNEL_TEST", "user_severity": "测试", "user_action": "无需操作"}
    else:
        event = choose_event(args.mode)
    if not event:
        state.update({"schema_version": "2.1", "updated_at": now().isoformat(timespec="seconds"), "notifications": notifications, "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]})
        write_json(state_path, state)
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0

    existing = find_existing_notification(notifications, event)
    if existing and existing.get("lifecycle_status") in {"SENT", "WAITING_CONFIRMATION", "CONFIRMED", "ARCHIVED"}:
        state.update({"schema_version": "2.1", "updated_at": now().isoformat(timespec="seconds"), "notifications": notifications, "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]})
        write_json(state_path, state)
        print(json.dumps({"status": "ALREADY_MANAGED", "notification_id": existing.get("notification_id"), "lifecycle_status": existing.get("lifecycle_status")}, ensure_ascii=False))
        return 0
    if existing and existing.get("lifecycle_status") == "EXPIRED":
        print(json.dumps({"status": "EXPIRED_REQUIRES_NEW_EVENT", "notification_id": existing.get("notification_id")}, ensure_ascii=False))
        return 0

    item = normalize_notification(event, existing)
    item["lifecycle_status"] = "CREATED"
    item["created_at"] = item.get("created_at") or now().isoformat(timespec="seconds")
    if not token:
        state.update({"schema_version": "2.1", "updated_at": now().isoformat(timespec="seconds"), "notifications": notifications, "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]})
        write_json(state_path, state)
        print(json.dumps({"status": "SKIPPED_NO_SECRET", "notification_id": item["notification_id"], "lifecycle_status": "CREATED"}, ensure_ascii=False))
        return 0

    ok, response = send(token, item["title"], item["content"])
    stamp = now().isoformat(timespec="seconds")
    item["last_attempted_at"] = stamp
    item["response"] = response
    item["lifecycle_status"] = "WAITING_CONFIRMATION" if ok and item["event_type"] in {"PENDING_EXECUTION_CONFIRMATION", "成交确认", "账户确认", "收盘账户"} else ("SENT" if ok else "CREATED")
    item["sent_at"] = stamp if ok else item.get("sent_at")
    if existing:
        notifications = [x for x in notifications if x.get("notification_id") != item["notification_id"]]
    notifications.append(item)
    state = {"schema_version": "2.1", "updated_at": stamp, "last_status": item["lifecycle_status"], "last_type": item["event_type"], "last_title": item["title"], "notifications": notifications[-HISTORY_LIMIT:], "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"], "policy": "通知必须让用户明确知道发生了什么、影响什么、是否需要行动；测试必须明确标注测试；历史失效故障不作为当前真实异常推送。", "safety_boundary": "通知中心不生成交易动作，不修改MASTER、风险许可、金额或卖出份额；正式成交只能由用户确认入口提交。"}
    write_json(state_path, state)
    print(json.dumps(item, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
