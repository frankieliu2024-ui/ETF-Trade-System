from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path
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
ROOT = Path(__file__).resolve().parents[1]
APAC_LATE_HK_MIN_DELTA_PCT = 0.8


def _formal_etf_aliases() -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Return formal ETF names and current provider aliases for user-visible text."""
    cfg = read_json(ROOT / "config/market/etf_monitor_universe.json", {})
    formal = {
        str(x.get("code") or ""): str(x.get("name") or "")
        for x in (cfg.get("objects") or [])
        if str(x.get("code") or "") and str(x.get("name") or "")
    }
    aliases: dict[tuple[str, str], str] = {}
    current = read_json(STATE / "CURRENT.json", {})
    snapshot_path = ROOT / str(current.get("latest_snapshot") or "")
    snapshot = read_json(snapshot_path, {}) if snapshot_path.exists() else {}
    for row in snapshot.get("rows") or []:
        code = str(row.get("symbol") or "")
        provider = str(row.get("provider_name") or "").strip()
        if code in formal and provider and provider != formal[code]:
            aliases[(provider, code)] = formal[code]
    return formal, aliases


def _normalize_user_visible_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(
        r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?\+08:00",
        r"\1 \2",
        text,
    )
    _formal, aliases = _formal_etf_aliases()
    for (provider, code), formal_name in aliases.items():
        text = text.replace(f"{provider}（{code}）", f"{formal_name}（{code}）")
    return text


def _normalize_user_visible_event(event: dict) -> dict:
    event = dict(event)
    event["title"] = _normalize_user_visible_text(event.get("title"))
    event["content"] = _normalize_user_visible_text(event.get("content"))
    formal, _aliases = _formal_etf_aliases()
    code = str(event.get("security_code") or "")
    if code in formal:
        event["security_name"] = formal[code]
    return event


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


def _current_a_share_inputs() -> tuple[dict[str, dict], list[dict], dict[str, dict]]:
    current = read_json(STATE / "CURRENT.json", {})
    snapshot_path = ROOT / str(current.get("latest_snapshot") or "")
    snapshot = read_json(snapshot_path, {}) if snapshot_path.exists() else {}
    rows = [x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"]
    indices = {str(x.get("symbol") or ""): x for x in rows if x.get("asset_class") == "A_SHARE_INDEX"}
    etfs = [x for x in rows if x.get("asset_class") == "ETF" and number(x.get("change_pct")) is not None]
    path_data = read_json(STATE / "intraday_path_features.json", {})
    features = {str(x.get("symbol") or ""): x for x in (path_data.get("features") or [])}
    return indices, etfs, features


def _is_a_share_session_summary(headline_lines: list[str]) -> bool:
    joined = " ".join(headline_lines)
    return "开盘/上午有价值信号" in joined or "15:00正式收盘" in joined


def render_summary(*, headline_lines: list[str], path_lines: list[str], implication: str, action: str, as_of_lines: list[str], boundary: str) -> str:
    """One user semantics for all session summaries.

    Producers still own data acquisition and event qualification. This layer owns
    what the user reads. For A-share session summaries it deliberately rebuilds
    the text from current market/account facts so old field-heavy producers cannot
    leak a machine-style report into PushPlus.
    """
    if _is_a_share_session_summary(headline_lines):
        try:
            from notification_semantics import a_share_structure

            indices, etfs, features = _current_a_share_inputs()
            if indices and etfs:
                headline_lines, path_lines, implication, action = a_share_structure(indices, etfs, features)
        except Exception as exc:
            print(f"notification semantic fallback: {exc}")

    path = "\n".join(path_lines[:3]) if path_lines else "- 当前没有额外路径事实需要展开。"
    return (
        "### 核心结论\n"
        + "\n".join(headline_lines[:5])
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


def _extract_object(what: list[str]) -> tuple[str, str, str, str]:
    joined = " ".join(what)
    object_line = next((x for x in what if "对象/结构" in x or "触发对象" in x), joined)
    clean = re.sub(r"^[-*\s]+", "", object_line)
    clean = re.sub(r"\*+", "", clean)
    clean = clean.split("：", 1)[-1].strip()
    match = re.search(r"（([A-Za-z0-9_]+)）", clean)
    code = match.group(1) if match else ""
    name = clean.split("（", 1)[0].strip() if clean else ""
    if "ETF" in clean:
        asset = "ETF"
    elif "指数" in clean or code in {"NDX", "SOX", "N225", "KOSPI", "TWII", "HSTECH"}:
        asset = "A_SHARE_INDEX" if code.isdigit() and len(code) == 6 else "INDEX"
    elif code.isdigit() and len(code) == 6:
        asset = "ACCOUNT_STOCK"
    else:
        asset = "OTHER"
    market = "A_SHARE" if code.isdigit() and len(code) == 6 or code.startswith("A_SHARE_") else "OVERSEAS"
    return code, name, asset, market


def _decision_readable_implication(what: list[str], implication: str, action: str) -> tuple[str, str]:
    """Central semantic gate for index/ETF/stock/overseas market alerts."""
    joined = " ".join(what)
    text = implication.strip()
    act = action.strip()
    should_override = (
        text.startswith("立即检查该新事实是否改变全部持仓ETF与观察ETF")
        or text.startswith("把本次新事实作为结构证据")
        or "相关ETF自身反馈、候选比较或下一节点准备" in text
    )
    if should_override:
        try:
            from notification_semantics import shock_implication

            code, name, asset, market = _extract_object(what)
            if name:
                return shock_implication(code, name, asset, market)
        except Exception as exc:
            print(f"shock semantic fallback: {exc}")

    if "HSTECH" not in joined and "恒生科技指数" not in joined:
        text = text.replace("；恒生科技指数（HSTECH）事件还要直接复核恒生科技ETF（513180）的自身反馈", "")
        text = text.replace("恒生科技指数（HSTECH）事件还要直接复核恒生科技ETF（513180）的自身反馈。", "")
    return text.strip(), act


def render_shock(*, what: list[str], why: str, implication: str, action: str, as_of: str, boundary: str) -> str:
    implication, action = _decision_readable_implication(what, implication, action)
    display_what = list(what[:5])
    if any("关键结构" in line and "当前" in line for line in display_what):
        display_what = [line for line in display_what if "当日涨跌" not in line and "当前涨跌" not in line]
    return (
        "### 核心结论\n"
        + "\n".join(display_what)
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


def _late_apac_update_error(event: dict) -> str:
    """Reject false HK-late updates caused only by data-coverage/tone drift.

    The 16:00+ APAC add-on is intentionally narrow: a missing primary summary may
    be recovered, otherwise the already-closed Japan/Korea/Taiwan facts cannot
    create a new regional direction by disappearing from the comparable set.
    A user-visible late update therefore requires a real HSTECH move of at least
    the production attention threshold from the primary APAC close.
    """
    if str(event.get("event_type") or "") != "APAC_SESSION_SUMMARY":
        return ""
    ctx = event.get("confirmation_context") or {}
    if str(ctx.get("session_node") or "") != "HK_LATE_UPDATE":
        return ""
    title = str(event.get("title") or "")
    if "主总结兜底" in title:
        return ""
    delta = number(ctx.get("hstech_change_since_primary_pct"))
    if delta is None:
        return "HK_LATE_UPDATE has no comparable HSTECH delta; coverage/tone drift is not material market change"
    if abs(delta) < APAC_LATE_HK_MIN_DELTA_PCT:
        return f"HK_LATE_UPDATE HSTECH delta {delta:+.2f}% is below {APAC_LATE_HK_MIN_DELTA_PCT:.2f}% materiality threshold"
    return ""


def _refine_reversal_user_title(event: dict) -> dict:
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
    event = _normalize_user_visible_event(event)
    event = _normalize_user_title(event)
    materiality_error = _late_apac_update_error(event)
    if materiality_error:
        return {"status": "REJECTED_NO_MATERIAL_APAC_CHANGE", "detail": materiality_error, "title": event.get("title")}
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
        stamp = now().isoformat(timespec="seconds")
        item["last_attempted_at"] = stamp
        item["response"] = {"error": "PUSHPLUS_TOKEN missing"}
        item["lifecycle_status"] = "FAILED"
        notifications = [x for x in notifications if x.get("notification_id") != item["notification_id"]]
        notifications.append(item)
        state.update({"schema_version": "2.2", "updated_at": stamp, "last_status": "FAILED", "last_type": item["event_type"], "last_title": item["title"], "notifications": notifications[-HISTORY_LIMIT:], "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"], "policy": policy})
        write_json(NOTIFICATION_STATE, state)
        return {"status": "FAILED", "notification_id": item.get("notification_id"), "response": item["response"]}
    ok, response = send(token, item["title"], item["content"])
    stamp = now().isoformat(timespec="seconds")
    item["last_attempted_at"] = stamp
    item["response"] = response
    item["lifecycle_status"] = "SENT" if ok else "FAILED"
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

