from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

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

NOTIFICATION_STATE = STATE / "notification_center.json"


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct(value: float | None) -> str:
    return "数据不可用" if value is None else f"{value:+.2f}%"


def price(value: float | None) -> str:
    if value is None:
        return "数据不可用"
    return f"{value:,.2f}" if abs(value) >= 100 else f"{value:.3f}"


def pct_change(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def range_position(close: float | None, high: float | None, low: float | None) -> float | None:
    if close is None or high is None or low is None or high <= low:
        return None
    return max(0.0, min(1.0, (close - low) / (high - low)))


def ohlc_path_phrase(open_: float | None, high: float | None, low: float | None, close: float | None) -> str:
    if None in (open_, high, low, close):
        return "日内路径信息不完整。"
    pos = range_position(close, high, low)
    from_open = pct_change(close, open_)
    from_low = pct_change(close, low)
    from_high = pct_change(close, high)
    if pos is not None and pos >= 0.8:
        ending = "收于日内区间高位"
    elif pos is not None and pos <= 0.2:
        ending = "收于日内区间低位"
    else:
        ending = "收于日内区间中部"
    return (
        f"开盘{price(open_)}，日内高{price(high)}、低{price(low)}，收/现价{price(close)}；"
        f"较开盘{pct(from_open)}，自低点修复{pct(from_low)}，距高点{pct(from_high)}，{ending}。"
    )


def render_summary(*, headline_lines: list[str], path_lines: list[str], implication: str, action: str, as_of_lines: list[str], boundary: str) -> str:
    return (
        "### 发生了什么\n"
        + "\n".join(headline_lines)
        + "\n\n### 日内路径\n"
        + "\n".join(path_lines)
        + "\n\n### 对ETF系统的启示\n"
        + implication
        + "\n\n### 现在怎么做\n**"
        + action
        + "**\n\n### 数据时点（北京时间）\n"
        + "\n".join(as_of_lines)
        + "\n\n### 解释边界\n"
        + boundary
    )


def _clean_market_implication(what: list[str], implication: str) -> str:
    """Remove object-specific boilerplate when it does not belong to this alert."""
    joined = " ".join(what)
    if "HSTECH" not in joined and "恒生科技指数" not in joined:
        implication = implication.replace("；恒生科技指数（HSTECH）事件还要直接复核恒生科技ETF（513180）的自身反馈", "")
        implication = implication.replace("恒生科技指数（HSTECH）事件还要直接复核恒生科技ETF（513180）的自身反馈。", "")
    return implication.strip()


def render_shock(*, what: list[str], why: str, implication: str, action: str, as_of: str, boundary: str) -> str:
    implication = _clean_market_implication(what, implication)
    # Market alerts should be decision-readable, not a generic monitoring report.
    # Put the actual event first, then the decision relevance and next action.
    return (
        "### 核心结论\n"
        + "\n".join(what)
        + "\n\n**判断**："
        + why
        + "\n\n### 对ETF系统的影响\n"
        + implication
        + "\n\n### 当前动作\n**"
        + action
        + "**\n\n### 数据与边界\n"
        + f"- **行情时点（北京时间）**：{as_of}\n"
        + f"- **边界**：{boundary}"
    )


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now().tzinfo)
    return dt.astimezone(now().tzinfo)


def _future_time_error(event: dict) -> str:
    ctx = event.get("confirmation_context") or {}
    # Market/provider facts may never be newer than the actual notification
    # generation time. A two-minute tolerance covers clock skew without allowing
    # screenshots such as "22:24 generated / 22:44 market data" to be sent.
    for key in (
        "market_as_of_beijing",
        "provider_observed_as_of_beijing",
        "a_share_as_of_beijing",
    ):
        dt = _parse_time(ctx.get(key))
        if dt and dt > now() + timedelta(minutes=2):
            return f"{key}={dt.isoformat(timespec='seconds')}"
    return ""


def _normalize_user_title(event: dict) -> dict:
    title = str(event.get("title") or "")
    if title.startswith("【异动提醒】"):
        event = dict(event)
        event["title"] = "【市场异动】" + title[len("【异动提醒】"):]
    return event


def persist_and_send(event: dict, *, policy: str) -> dict:
    event = _normalize_user_title(event)
    future_error = _future_time_error(event)
    if future_error:
        return {"status": "REJECTED_FUTURE_MARKET_TIME", "detail": future_error, "title": event.get("title")}

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
        "policy": policy,
        "safety_boundary": "通知只总结已取得的行情、账户、研究或系统事实；不生成自动交易，不修改MASTER、风险许可、金额或卖出份额。",
    })
    write_json(NOTIFICATION_STATE, state)
    return {"status": item["lifecycle_status"], "notification_id": item["notification_id"], "title": item["title"], "response": response}
