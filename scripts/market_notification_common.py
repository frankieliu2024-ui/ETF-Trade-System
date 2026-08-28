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
    """Unified user-facing structure for market/session summaries.

    Data producers may remain market-specific, but user-visible semantics do not:
    conclusion -> meaningful path -> ETF-system impact -> current action -> data/boundary.
    """
    path = "\n".join(path_lines) if path_lines else "- 当前没有额外路径事实需要展开。"
    return (
        "### 核心结论\n"
        + "\n".join(headline_lines)
        + "\n\n### 关键路径\n"
        + path
        + "\n\n### 对ETF系统的影响\n"
        + implication
        + "\n\n### 当前动作\n**"
        + action
        + "**\n\n### 数据与边界\n"
        + "\n".join(as_of_lines)
        + "\n"
        + f"- **边界**：{boundary}"
    )


def _decision_readable_implication(what: list[str], implication: str) -> str:
    """Turn generic market boilerplate into object-aware ETF decision relevance.

    This is deliberately centralized so A-share indices, all monitored ETFs,
    account/conditional stocks, APAC objects and US objects share one user
    semantics while their acquisition/workflow routes remain independent.
    """
    joined = " ".join(what)
    text = implication.strip()

    # A-share unified event engine currently passes this generic sentence for
    # indices, ETFs and account/conditional stocks. Resolve it by object role.
    if text.startswith("立即检查该新事实是否改变全部持仓ETF与观察ETF"):
        if "ETF" in joined:
            return (
                "这是ETF自身结构变化，不是泛化市场提示。优先复核该ETF相对全部持仓/观察ETF的强弱是否改变，"
                "当前Trial或持仓假设是否被削弱，以及继续占用下一单位资本是否仍有效率；"
                "只有这些结论改变时，才影响唯一主候选、金额或持仓动作。"
            )
        if "指数" in joined:
            return (
                "这是A股本地风险偏好/风格结构证据。先判断变化是否扩散到监测ETF，再比较哪些持仓/观察ETF的"
                "承接和相对强弱真正发生变化；指数异动本身不直接生成买卖动作。"
            )
        return (
            "这是账户底仓或条件个股的资金/产业传导证据。优先复核其独立假设、资金释放价值以及是否改变ETF资本比较；"
            "个股单一波动不直接生成ETF买卖动作。"
        )

    # Overseas/US/Asia unified value-event engine passes this generic sentence.
    if text.startswith("把本次新事实作为结构证据"):
        direct = ""
        if "恒生科技指数" in joined or "HSTECH" in joined:
            direct = "直接相关的恒生科技ETF（513180）"
        elif "日经225指数" in joined or "N225" in joined:
            direct = "直接相关的日经ETF（513520）"
        elif any(x in joined for x in ("纳斯达克100", "NDX", "费城半导体", "SOX", "QQQ", "SOXX")):
            direct = "A股科技持仓/观察ETF以及纳指ETF（159941）"
        target = direct or "相关持仓/观察ETF"
        return (
            f"这是海外/区域结构证据，先看A股本地价格是否接受或背离，再复核{target}的自身反馈、"
            "当前主候选和边际资本效率；若海外与A股反馈背离，以A股自身反馈为主。"
        )

    # Keep already-specific producer text, while removing the old HSTECH-only
    # appendage if it leaked into a non-HSTECH alert.
    if "HSTECH" not in joined and "恒生科技指数" not in joined:
        text = text.replace("；恒生科技指数（HSTECH）事件还要直接复核恒生科技ETF（513180）的自身反馈", "")
        text = text.replace("恒生科技指数（HSTECH）事件还要直接复核恒生科技ETF（513180）的自身反馈。", "")
    return text.strip()


def render_shock(*, what: list[str], why: str, implication: str, action: str, as_of: str, boundary: str) -> str:
    implication = _decision_readable_implication(what, implication)
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
    for key in (
        "market_as_of_beijing",
        "provider_observed_as_of_beijing",
        "a_share_as_of_beijing",
    ):
        dt = _parse_time(ctx.get(key))
        if dt and dt > now() + timedelta(minutes=2):
            return f"{key}={dt.isoformat(timespec='seconds')}"
    return ""


def _refine_reversal_user_title(event: dict) -> dict:
    """Keep event family stable while making visible path semantics precise."""
    ctx = event.get("confirmation_context") or {}
    if str(ctx.get("event_category") or "") != "REVERSAL":
        return event
    title = str(event.get("title") or "")
    if "日内方向明显反转" not in title:
        return event
    day = number(ctx.get("day_change_pct"))
    direction = str(ctx.get("direction") or "")
    replacement = ""
    if direction == "DOWN" and day is not None and day >= 0:
        replacement = "冲高明显回吐"
    elif direction == "UP" and day is not None and day <= 0:
        replacement = "下探明显修复"
    if not replacement:
        return event
    event = dict(event)
    event["title"] = title.replace("日内方向明显反转", replacement)
    return event


def _normalize_user_title(event: dict) -> dict:
    event = _refine_reversal_user_title(event)
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
