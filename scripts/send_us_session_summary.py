from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from notification_center import (
    HISTORY_LIMIT,
    STATE,
    compact_recent,
    expire_notifications,
    find_existing_notification,
    normalize_notification,
    now,
    read_json,
    send,
    write_json,
)

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CONTEXT = STATE / "us_extended_hours_context.json"
NOTIFICATION_STATE = STATE / "notification_center.json"
NEW_YORK = ZoneInfo("America/New_York")
BEIJING = ZoneInfo("Asia/Shanghai")


def _fmt_pct(value: float | None) -> str:
    return "数据不可用" if value is None else f"{value:+.2f}%"


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_tone(qqq: float | None, soxx: float | None) -> tuple[str, str]:
    values = [x for x in (qqq, soxx) if x is not None]
    if len(values) < 2:
        return "信息不足", "两项核心代理未同时取得有效数据，本次不把不完整信息解释为明确海外结构。"
    if qqq * soxx < 0 and abs(qqq - soxx) >= 0.75:
        return "明显分化", "纳指100与半导体方向分化，不能把单一美股方向机械映射到A股科技ETF。"
    avg = sum(values) / len(values)
    if avg >= 1.5:
        return "明显偏强", "外部科技风险偏好明显增强；下一A股交易节点优先观察科创、半导体、通信等科技方向是否出现同向本地反馈。"
    if avg >= 0.5:
        return "偏强", "外部科技结构偏强；下一A股交易节点把科技方向列为重点验证对象，但仍需A股自身反馈确认。"
    if avg <= -1.5:
        return "明显偏弱", "外部科技风险偏好明显走弱；下一A股交易节点优先复核高相关科技篮子的承接、风险收益与风险许可，但不机械卖出。"
    if avg <= -0.5:
        return "偏弱", "外部科技结构偏弱；下一A股交易节点提高对科技持仓承接与相对强弱的关注，但不把海外下跌直接等同于A股卖出。"
    return "相对平稳", "外部科技结构没有形成足以单独改变A股交易判断的强信号；作为下一A股盘前背景保存。"


def _window(now_et: datetime, phase: str) -> str:
    minute = now_et.hour * 60 + now_et.minute
    if phase == "REGULAR" and 9 * 60 + 35 <= minute <= 10 * 60:
        return "OPEN"
    if phase == "POST_MARKET" and 16 * 60 + 5 <= minute <= 16 * 60 + 30:
        return "CLOSE"
    return ""


def build_event() -> dict | None:
    context = read_json(CONTEXT, {})
    objects = context.get("objects") or {}
    qqq = objects.get("QQQ") or {}
    soxx = objects.get("SOXX") or {}
    if not qqq or not soxx:
        return None
    if qqq.get("quality_status") not in {"PASS", "FRESH"} or soxx.get("quality_status") not in {"PASS", "FRESH"}:
        return None

    now_et = datetime.now(timezone.utc).astimezone(NEW_YORK)
    phase = str(qqq.get("current_market_phase") or "")
    if phase != str(soxx.get("current_market_phase") or ""):
        return None
    node = _window(now_et, phase)
    if not node:
        return None

    qqq_date = str(qqq.get("regular_session_market_date") or (qqq.get("latest") or {}).get("market_date_local") or "")
    soxx_date = str(soxx.get("regular_session_market_date") or (soxx.get("latest") or {}).get("market_date_local") or "")
    if not qqq_date or qqq_date != soxx_date:
        return None

    qqq_change = _number(qqq.get("regular_session_change_vs_previous_close_pct"))
    soxx_change = _number(soxx.get("regular_session_change_vs_previous_close_pct"))
    if qqq_change is None or soxx_change is None:
        return None
    tone, implication = _market_tone(qqq_change, soxx_change)

    latest_times = [
        str((qqq.get("latest") or {}).get("as_of_beijing") or ""),
        str((soxx.get("latest") or {}).get("as_of_beijing") or ""),
    ]
    latest_times = [x for x in latest_times if x]
    as_of = max(latest_times) if latest_times else str(context.get("generated_at_beijing") or "")
    generated = now().strftime("%Y-%m-%d %H:%M:%S")

    if node == "OPEN":
        title = f"【美股开盘｜次日A股参考】科技风险偏好{tone}"
        node_name = "美股现金盘开盘观察"
        interpretation = (
            "这是美股开盘后首个稳定观察窗口，不是盘前价格，也不是全天结论。"
            "QQQ/SOXX作为纳指100与半导体方向代理，用于判断外部科技结构；不会单独生成A股买卖动作。"
        )
        next_step = "今晚无需立即下单；把本节点作为下一A股交易日盘前的外部结构证据，届时继续验证A股指数、目标ETF自身反馈与资本效率。"
    else:
        title = f"【美股收盘｜次日A股参考】科技风险偏好{tone}"
        node_name = "美股现金盘收盘总结"
        interpretation = (
            "这是美股现金盘当日收盘方向总结。盘后价格若继续变化，仍只作为扩展时段前置信号；"
            "下一A股交易日正式判断必须继续经过本地传导和目标ETF自身反馈。"
        )
        next_step = "将本次收盘结构直接纳入下一A股交易日盘前分析；无需在夜间机械调整A股持仓，除非另有明确账户、风险或正式交易通知。"

    content = (
        "### 发生了什么\n"
        f"- **节点**：{node_name}\n"
        f"- **纳指100ETF代理（QQQ）**：较上一现金盘收盘 {_fmt_pct(qqq_change)}\n"
        f"- **半导体ETF代理（SOXX）**：较上一现金盘收盘 {_fmt_pct(soxx_change)}\n"
        f"- **综合判断**：{tone}\n\n"
        "### 对A股ETF系统的启示\n"
        f"{implication}\n\n"
        "### 现在怎么做\n"
        f"**{next_step}**\n\n"
        "### 解释边界\n"
        f"{interpretation}\n\n"
        "### 数据时点（北京时间）\n"
        f"- **行情依据**：{as_of or '未提供'}\n"
        f"- **通知生成**：{generated}\n\n"
        "> 海外结构 → 本地传导 → ETF自身反馈 → 机会判断。美股总结只提供海外证据，不修改MASTER、不扩大交易权限、不自动下单。"
    )

    return {
        "key": f"us-session-summary:{qqq_date}:{node}",
        "type": "美股节点总结",
        "event_type": "US_SESSION_SUMMARY",
        "title": title,
        "content": content,
        "source": "us_extended_hours_context",
        "user_severity": "需要关注",
        "user_action": "纳入下一A股交易节点的海外结构判断，无需立即机械交易",
        "confirmation_context": {
            "us_market_date": qqq_date,
            "session_node": node,
            "qqq_change_pct": qqq_change,
            "soxx_change_pct": soxx_change,
            "tone": tone,
            "market_as_of_beijing": as_of,
        },
    }


def persist_and_send(event: dict) -> dict:
    state = read_json(NOTIFICATION_STATE, {"schema_version": "2.2", "notifications": [], "recent": []})
    raw_items = list(state.get("notifications") or [])
    if not raw_items:
        raw_items = [normalize_notification(x, x) for x in (state.get("recent") or [])]
    notifications = expire_notifications(raw_items)
    existing = find_existing_notification(notifications, event)
    if existing and existing.get("lifecycle_status") in {"SENT", "WAITING_CONFIRMATION", "CONFIRMED", "ARCHIVED"}:
        return {"status": "ALREADY_MANAGED", "notification_id": existing.get("notification_id")}

    item = normalize_notification(event, existing)
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return {"status": "SKIPPED_NO_SECRET", "notification_id": item.get("notification_id")}

    ok, response = send(token, item["title"], item["content"])
    stamp = now().isoformat(timespec="seconds")
    item["last_attempted_at"] = stamp
    item["response"] = response
    item["lifecycle_status"] = "SENT" if ok else "CREATED"
    item["sent_at"] = stamp if ok else item.get("sent_at")
    if existing:
        notifications = [x for x in notifications if x.get("notification_id") != item["notification_id"]]
    notifications.append(item)
    state.update({
        "schema_version": "2.2",
        "updated_at": stamp,
        "last_status": item["lifecycle_status"],
        "last_type": item["event_type"],
        "last_title": item["title"],
        "notifications": notifications[-HISTORY_LIMIT:],
        "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]],
        "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"],
        "policy": "有独立行动/关注价值的实质事件可在任何时段立即推送；普通行情、研究、维护成功和无行动价值变化只后台记录；美股开盘/收盘各形成一次总结性海外结构通知，作为下一A股交易节点证据，不直接生成A股动作。",
        "safety_boundary": "通知只转发已有行情/状态事实和总结性启示，不生成自动交易，不修改MASTER、风险许可、金额或卖出份额。",
    })
    write_json(NOTIFICATION_STATE, state)
    return {"status": item["lifecycle_status"], "notification_id": item["notification_id"], "title": item["title"], "response": response}


def main() -> int:
    event = build_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(event)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
