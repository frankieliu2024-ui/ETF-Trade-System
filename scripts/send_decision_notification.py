from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
TRIGGER = STATE / "decision_trigger.json"
ACCOUNT = STATE / "account_fact.json"
CURRENT = STATE / "CURRENT.json"
NOTIFICATION = STATE / "decision_notification.json"
TZ = timezone(timedelta(hours=8))
PUSHPLUS_URL = "https://www.pushplus.plus/send"
MAX_RETRIES_PER_EVENT = 2


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


def _display_from_code(code: str, account: dict) -> str:
    if not code:
        return "相关交易对象"
    for p in account.get("positions") or []:
        if str(p.get("code") or "") == code:
            name = str(p.get("name") or "")
            return f"{name}（{code}）" if name else code
    return code


def _trade_display(event_id: str) -> str:
    if not event_id:
        return "相关交易对象"
    path = ROOT / "events" / "trades" / f"{event_id}.json"
    event = _read(path, {})
    name = str(event.get("name") or event.get("security_name") or "")
    code = str(event.get("code") or event.get("security_code") or event.get("symbol") or "")
    if name and code:
        return f"{name}（{code}）"
    return name or code or "相关交易对象"


def _risk_text(raw: str) -> str:
    mapping = {
        "NORMAL": "正常区间",
        "RISK_OBSERVATION": "风险观察区间",
        "RISK_CONTROL": "风险控制区间",
        "RISK_CONTROL_REVIEW": "强化风险复核区间",
    }
    return mapping.get(raw, "新的风险区间")


def _human_message(trigger: dict, account: dict) -> tuple[str, str]:
    event_type = str(trigger.get("trigger_type") or "")
    applicable = str(trigger.get("applicable_object") or "")
    evidence = str(trigger.get("evidence_change") or "")

    if event_type == "TRADE_CONFIRMED":
        target = _trade_display(applicable)
        return (
            f"{target}成交已确认，建议重新评估",
            f"{target}刚刚出现已确认成交，账户持仓或现金结构已经发生变化。\n\n建议：现在重新检查持仓、资金和下一步交易安排。",
        )

    if event_type == "ACCOUNT_STRUCTURE_CHANGED":
        target = _display_from_code(applicable, account)
        return (
            "账户状态发生重要变化",
            f"{target}相关的账户持仓、现金或资产结构出现已确认变化。\n\n建议：现在重新检查当前持仓和可用资金，确认是否需要调整原交易判断。",
        )

    if event_type == "RISK_BOUNDARY_CROSSED":
        parts = evidence.split("→", 1)
        new_zone = _risk_text(parts[1]) if len(parts) == 2 else "新的风险区间"
        return (
            "ETF策略风险状态发生变化",
            f"ETF策略风险状态已经进入{new_zone}，这可能改变新增交易的可用空间。\n\n建议：现在重新评估风险许可和当前交易计划。",
        )

    if event_type == "E2E_RECOVERED":
        return (
            "此前暂缓的交易判断现在可以继续",
            "此前因为关键行情、账户或决策信息不完整而暂缓的交易判断，现在所需信息已经补齐。\n\n建议：重新处理之前尚未完成的交易判断。",
        )

    if event_type == "MATERIAL_EVIDENCE_CHANGED":
        target = _display_from_code(applicable, account)
        return (
            f"{target}出现值得重新评估的新证据",
            f"{target}出现了可能改变原交易判断的新市场或研究证据。\n\n建议：现在重新检查该标的的机会、持仓或风险收益判断。",
        )

    return (
        "ETF交易判断需要重新评估",
        "系统发现了可能改变原交易动作的新信息。\n\n建议：现在重新检查相关ETF的交易判断。",
    )


def _state(trigger: dict, status: str, **extra: Any) -> dict:
    base = {
        "schema_version": "1.0",
        "updated_at": _now(),
        "status": status,
        "trigger_idempotency_key": str(trigger.get("idempotency_key") or ""),
        "trigger_type": str(trigger.get("trigger_type") or ""),
        "channel": "PUSHPLUS_WECHAT",
        "user_message_language": "PLAIN_CHINESE",
        "safety_boundary": "通知层只传递需要用户关注的重评事件，不生成交易动作，不修改MASTER、风险许可、金额或卖出份额。",
    }
    base.update(extra)
    return base


def _send(token: str, title: str, content: str) -> tuple[bool, dict]:
    payload = json.dumps({
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
        "channel": "wechat",
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        PUSHPLUS_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ETF-Trade-System/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"raw": raw[:500]}
            ok = int(body.get("code", 0) or 0) == 200
            return ok, {"http_status": response.status, "pushplus_code": body.get("code"), "pushplus_message": body.get("msg")}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, {"error": type(exc).__name__, "message": str(exc)[:300]}


def main() -> int:
    trigger = _read(TRIGGER, {})
    prior = _read(NOTIFICATION, {})
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    key = str(trigger.get("idempotency_key") or "")
    trigger_status = str(trigger.get("status") or "")
    requires = bool(trigger.get("requires_formal_reassessment"))

    eligible_status = trigger_status in {"TRIGGERED", "ALREADY_RECORDED"}
    prior_same = bool(key) and key == str(prior.get("trigger_idempotency_key") or "")
    prior_status = str(prior.get("status") or "")
    retry_count = int(prior.get("retry_count") or 0) if prior_same else 0

    if not key or not eligible_status or (not requires and not (prior_same and prior_status == "FAILED")):
        state = _state(trigger, "NO_NOTIFICATION_NEEDED", reason="没有新的、需要用户处理的正式重评事件。", retry_count=retry_count)
        _write(NOTIFICATION, state)
        print(json.dumps(state, ensure_ascii=False))
        return 0

    if prior_same and prior_status == "SENT":
        print(json.dumps(prior, ensure_ascii=False))
        return 0

    if prior_same and retry_count >= MAX_RETRIES_PER_EVENT:
        state = _state(trigger, "FAILED_RETRY_EXHAUSTED", reason="同一事件的主动通知已连续失败两次，等待维护诊断。", retry_count=retry_count)
        _write(NOTIFICATION, state)
        print(json.dumps(state, ensure_ascii=False))
        return 2

    if not token:
        state = _state(trigger, "SKIPPED_NO_SECRET", reason="PUSHPLUS_TOKEN未提供；交易主链继续运行。", retry_count=retry_count)
        _write(NOTIFICATION, state)
        print(json.dumps(state, ensure_ascii=False))
        return 0

    account = _read(ACCOUNT, {})
    title, content = _human_message(trigger, account)
    ok, response = _send(token, title, content)
    retry_count += 1

    if ok:
        state = _state(
            trigger,
            "SENT",
            title=title,
            sent_at=_now(),
            retry_count=retry_count,
            response=response,
        )
        _write(NOTIFICATION, state)
        print(json.dumps(state, ensure_ascii=False))
        return 0

    state = _state(
        trigger,
        "FAILED",
        title=title,
        failed_at=_now(),
        retry_count=retry_count,
        response=response,
        reason="PushPlus发送失败；不阻塞行情和交易判断主链，下一次状态构建允许重试。",
    )
    _write(NOTIFICATION, state)
    print(json.dumps(state, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
