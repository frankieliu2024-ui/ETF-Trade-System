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
    payload = json.dumps({
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
        "channel": "wechat",
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        PUSHPLUS_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ETF-Trade-System/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body_raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(body_raw)
            except json.JSONDecodeError:
                body = {"raw": body_raw[:500]}
            ok = int(body.get("code", 0) or 0) == 200
            return ok, {
                "http_status": response.status,
                "pushplus_code": body.get("code"),
                "pushplus_message": body.get("msg"),
            }
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
    if name and code:
        return f"{name}（{code}）"
    return name or code or "相关交易对象"


def execution_confirmation_event() -> dict | None:
    recon = read_json(STATE / "execution_reconciliation.json", {})
    if str(recon.get("status") or "") != "CONFIRMATION_REQUIRED":
        return None
    matches = [x for x in (recon.get("matches") or []) if x.get("requires_user_confirmation")]
    if not matches:
        return None
    match = matches[0]
    intent = match.get("intent") or {}
    code = str(intent.get("code") or "")
    name = str(intent.get("name") or "")
    target = f"{name}（{code}）" if name and code else (code or "相关ETF")
    decision_id = str(intent.get("decision_id") or "")
    suggested_date = str(match.get("suggested_execution_date") or intent.get("decision_market_date") or "")
    qty = (match.get("observed_account_change") or {}).get("quantity_increase")
    qty_text = f"，新增约{int(qty):,}份" if isinstance(qty, (int, float)) and qty > 0 else ""
    if str(match.get("status") or "") == "UNLINKED_BUY_TRADE_REQUIRES_ATTRIBUTION":
        content = f"发现{target}存在已确认买入成交，但还没有明确归因到此前的Trial决策。\n\n建议：请在ETF交易会话确认这笔买入是否属于{suggested_date or '此前'}的5,000元Trial。"
    else:
        content = f"最新账户信息显示{target}{qty_text}，与{suggested_date or '此前'}的5,000元Trial决策高度匹配。\n\n建议：请在ETF交易会话确认是否按该Trial执行，以及实际交易日。系统会把交易日与今天的截图/确认日期分开记录。"
    key = f"execution-confirm:{decision_id}:{code}:{suggested_date}:{qty or ''}"
    return {
        "key": key,
        "type": "成交确认",
        "title": f"发现{target}可能已执行Trial，请确认",
        "content": content,
        "source": "execution_reconciliation",
    }


def decision_event() -> dict | None:
    trigger = read_json(STATE / "decision_trigger.json", {})
    if not trigger.get("requires_formal_reassessment"):
        return None
    if str(trigger.get("status") or "") not in {"TRIGGERED", "ALREADY_RECORDED"}:
        return None
    key = str(trigger.get("idempotency_key") or "")
    if not key:
        return None
    account = read_json(STATE / "account_fact.json", {})
    event_type = str(trigger.get("trigger_type") or "")
    applicable = str(trigger.get("applicable_object") or "")

    if event_type == "TRADE_CONFIRMED":
        target = trade_display(applicable)
        title = f"{target}成交后需要重新评估"
        content = f"{target}的成交已经确认，账户持仓或现金结构发生变化。\n\n建议：现在重新检查持仓、资金和下一步交易安排。"
    elif event_type == "ACCOUNT_STRUCTURE_CHANGED":
        target = display_from_code(applicable, account)
        title = "账户状态发生重要变化"
        content = f"{target}相关的持仓、现金或资产结构出现已确认变化。\n\n建议：现在重新检查当前持仓和可用资金，确认是否需要调整原交易判断。"
    elif event_type == "RISK_BOUNDARY_CROSSED":
        title = "ETF策略风险状态发生变化"
        content = "ETF策略风险状态已跨过关键区间，这可能改变新增交易的可用空间。\n\n建议：现在重新评估风险许可和当前交易计划。"
    elif event_type == "E2E_RECOVERED":
        title = "此前暂缓的交易判断现在可以继续"
        content = "此前因为关键行情、账户或决策信息不完整而暂缓的交易判断，现在所需信息已经补齐。\n\n建议：重新处理之前尚未完成的交易判断。"
    else:
        target = display_from_code(applicable, account)
        title = f"{target}出现值得重新评估的新变化"
        content = f"{target}出现了可能改变原交易判断的新市场或研究证据。\n\n建议：现在重新检查该标的的机会、持仓或风险收益判断。"
    return {"key": f"decision:{key}", "type": "交易判断", "title": title, "content": content, "source": "decision_trigger"}


def account_confirmation_event() -> dict | None:
    account = read_json(STATE / "account_fact.json", {})
    events = account.get("account_change_events_after_confirmed_at") or []
    unresolved = [e for e in events if str(e.get("reconciliation_status") or "").upper() == "UNRECONCILED_ACCOUNT_CHANGE"]
    if not unresolved:
        return None
    event = unresolved[-1]
    code = str(event.get("code") or event.get("object") or "")
    target = display_from_code(code, account)
    event_time = str(event.get("event_time") or event.get("occurred_at") or account.get("updated_at") or "")
    key = f"account:{code}:{event_time}:{event.get('change_summary','')}"
    return {
        "key": key,
        "type": "账户确认",
        "title": "发现一项需要你确认的账户变化",
        "content": f"{target}相关的账户变化暂时无法由已确认成交或其他已知事件解释。\n\n建议：请确认是否存在未记录成交、资金划转或其他账户变化；必要时上传当前账户截图。",
        "source": "account_fact",
    }


def system_event() -> dict | None:
    heal = read_json(STATE / "self_healing_status.json", {})
    classification = str(heal.get("classification") or "")
    action = str(heal.get("recommended_action") or "")
    if action == "ESCALATE" or classification in {"PERSISTENT_RUNTIME_FAILURE", "CONSISTENCY_REGRESSION"}:
        key = f"system:selfheal:{classification}:{heal.get('checked_at') or heal.get('updated_at') or ''}"
        return {
            "key": key,
            "type": "系统异常",
            "title": "ETF系统出现持续异常，需要关注",
            "content": "行情或系统状态连续异常，自动修复已经到达安全边界。\n\n影响：当前交易判断的可靠性可能下降。建议：暂缓依赖系统进行新的交易判断，待异常恢复后再继续。",
            "source": "self_healing_status",
        }

    diag = read_json(STATE / "workflow_failure_diagnostic.json", {})
    if str(diag.get("recommended_action") or "") == "ESCALATE_WITH_DIAGNOSTIC":
        run_id = str(diag.get("run_id") or "")
        classification = str(diag.get("classification") or "")
        key = f"system:workflow:{run_id}:{classification}"
        return {
            "key": key,
            "type": "系统异常",
            "title": "ETF系统有一项故障未能自动处理",
            "content": "后台维护发现一项无法安全自动修复的故障。\n\n如果该故障影响行情、账户或交易判断，系统将保持谨慎降级；建议稍后检查恢复情况。",
            "source": "workflow_failure_diagnostic",
        }
    return None


def close_account_event(force: bool = False) -> dict | None:
    current = read_json(STATE / "CURRENT.json", {})
    account = read_json(STATE / "account_fact.json", {})
    market_date = str(current.get("market_date") or "")
    if not market_date:
        return None
    dt = now()
    if not force and (dt.weekday() >= 5 or dt.hour < 15):
        return None
    updated = str(account.get("updated_at") or "")
    confirmed_date = str(account.get("last_confirmed_market_date") or "")
    final_confirmed = False
    if confirmed_date == market_date and updated:
        try:
            stamp = datetime.fromisoformat(updated)
            final_confirmed = stamp.hour >= 15
        except ValueError:
            pass
    if final_confirmed:
        return None
    key = f"close-account:{market_date}"
    return {
        "key": key,
        "type": "收盘账户",
        "title": "今日收盘账户信息尚未确认",
        "content": "系统还没有今天15:00之后的最终账户信息。\n\n建议：请上传收盘持仓截图；如果今天15:00后账户没有任何变化，也可以直接确认“收盘账户无变化”。",
        "source": "account_fact",
    }


def choose_event(mode: str) -> dict | None:
    if mode == "close":
        return close_account_event()
    if mode == "close-test":
        return close_account_event(force=True)
    # First ask for the smallest missing human fact. Then surface system faults and decision reassessment.
    for builder in (execution_confirmation_event, account_confirmation_event, system_event, decision_event):
        event = builder()
        if event:
            return event
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["event", "close", "close-test", "channel-test"], default="event")
    args = parser.parse_args()
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    state_path = STATE / "notification_center.json"
    state = read_json(state_path, {"schema_version": "1.0", "recent": []})
    recent = state.get("recent") or []
    sent_keys = {str(x.get("key") or "") for x in recent if str(x.get("status") or "") == "SENT"}

    if args.mode == "channel-test":
        event = {
            "key": f"channel-test:{now().isoformat(timespec='seconds')}",
            "type": "测试",
            "title": "ETF系统通知中心测试",
            "content": "通知中心已经可以主动联系你。正式运行时，只推送需要你关注、确认或决策的事项。",
            "source": "manual_test",
        }
    else:
        event = choose_event(args.mode)

    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    if event["key"] in sent_keys:
        print(json.dumps({"status": "ALREADY_SENT", "key": event["key"]}, ensure_ascii=False))
        return 0
    if not token:
        print(json.dumps({"status": "SKIPPED_NO_SECRET", "key": event["key"]}, ensure_ascii=False))
        return 0

    ok, response = send(token, event["title"], event["content"])
    record = {
        **event,
        "status": "SENT" if ok else "FAILED",
        "attempted_at": now().isoformat(timespec="seconds"),
        "response": response,
    }
    recent.append(record)
    state = {
        "schema_version": "1.1",
        "updated_at": now().isoformat(timespec="seconds"),
        "last_status": record["status"],
        "last_type": record["type"],
        "last_title": record["title"],
        "recent": recent[-HISTORY_LIMIT:],
        "policy": "只推送需要用户关注、确认或决策的事项；延迟上传账户截图时优先提示疑似执行匹配；普通行情刷新、成功自愈和普通后台运行不推送。",
        "safety_boundary": "通知中心不生成交易动作，不修改MASTER、风险许可、金额或卖出份额。",
    }
    write_json(state_path, state)
    print(json.dumps(record, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
