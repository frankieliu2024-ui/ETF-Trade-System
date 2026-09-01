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
    parse_notification_time,
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
                is_close = any("15:00正式收盘" in line for line in headline_lines)
            headline_lines, path_lines, implication, action = a_share_structure(indices, etfs, features, is_close=is_close)
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


def _market_event_staleness_error(event: dict) -> str:
    """Reject old market facts from the live-event channel.

    Replay/recovery is still possible, but it must opt in explicitly so an old
    snapshot cannot look like a newly observed market move.
    """
    if str(event.get("event_type") or "") not in MARKET_EVENT_TYPES:
        return ""
    ctx = _market_context(event)
    if str(ctx.get("notification_mode") or "").upper() in {"RECOVERY", "BACKFILL"}:
        return ""
    observed = _parse_time(ctx.get("market_as_of_beijing") or ctx.get("provider_observed_as_of_beijing") or ctx.get("event_time_beijing"))
    if not observed:
        return ""
    policy = read_json(ROOT / "config" / "runtime_policy.json", {})
    max_age = int(policy.get("stale_after_seconds", policy.get("degraded_max_age_seconds", 1500)))
    age = (now() - observed).total_seconds()
    if age > max_age:
        return f"market fact is {int(age)} seconds old; live notification requires explicit recovery mode"
    return ""


def _annotate_summary_increment(event: dict, notifications: list[dict]) -> dict:
    """Make a post-cutoff fact explicit instead of presenting two conflicting claims."""
    if str(event.get("event_type") or "") not in MARKET_EVENT_TYPES or _is_protected_event(event):
        return event
    event_time = _parse_time(_market_context(event).get("market_as_of_beijing"))
    if not event_time:
        return event
    for summary in reversed(notifications):
        if str(summary.get("event_type") or "") not in {"A_SHARE_SESSION_SUMMARY", "APAC_SESSION_SUMMARY", "US_SESSION_SUMMARY"}:
            continue
        sctx = _market_context(summary)
        cutoff = _parse_time(sctx.get("market_as_of_beijing"))
        sent = _parse_time(summary.get("sent_at") or summary.get("created_at"))
        if cutoff and sent and cutoff < event_time <= sent + timedelta(minutes=SUMMARY_ABSORB_MINUTES):
            event = dict(event)
            ctx = dict(_market_context(event))
            ctx["summary_relation"] = "NEW_FACT_AFTER_SUMMARY_CUTOFF"
            event["confirmation_context"] = ctx
            event["content"] = str(event.get("content") or "") + f"\n\n> 这是收盘总结数据截止{cutoff.isoformat(timespec='seconds')}后的新增事实；不否定此前总结，仅作为增量变化展示。"
            return event
    return event


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


SUMMARY_ABSORB_MINUTES = 5
AGGREGATION_WINDOW_MINUTES = 5
MARKET_EVENT_TYPES = {"MARKET_SHOCK_ALERT", "MARKET_VALUE_ALERT", "APAC_OPEN_SIGNAL"}
PROTECTED_EVENT_TYPES = {"FORMAL_DECISION_MATERIAL_CHANGE", "ACCOUNT_FACT_CONFIRMATION", "PENDING_EXECUTION_CONFIRMATION", "交易判断", "风险许可", "持仓动作", "Trial机会", "Confirm机会", "机会失效"}
MERGEABLE_CATEGORIES = {"SUDDEN", "EXTREME", "REVERSAL"}


def _market_context(event: dict) -> dict:
    return event.get("confirmation_context") or {}


def _market_fact_key(event: dict) -> str:
    ctx = _market_context(event)
    explicit = str(ctx.get("fact_key") or event.get("fact_key") or "")
    if explicit:
        return explicit
    return "|".join(str(ctx.get(k) or event.get(k) or "") for k in ("market_date", "security_code", "event_category", "direction"))


def _is_protected_event(event: dict) -> bool:
    event_type = str(event.get("event_type") or event.get("type") or "")
    title = str(event.get("title") or "")
    return event_type in PROTECTED_EVENT_TYPES or any(title.startswith(f"【{x}") for x in ("Trial机会", "Confirm机会", "机会失效", "持仓动作", "风险许可"))


def _event_magnitude(event: dict) -> float | None:
    ctx = _market_context(event)
    for key in ("event_magnitude_pct", "covered_event_magnitude_pct", "day_change_pct", "phase_metric_change_pct", "sudden_change_pct"):
        value = number(ctx.get(key))
        if value is not None:
            return abs(value)
    return None


def _is_material_upgrade(event: dict, prior: dict) -> bool:
    current, old = _event_magnitude(event), _event_magnitude(prior)
    return current is not None and old is not None and current >= old + max(0.5, old * 0.35)


def _summary_covers_event(summary: dict, event: dict) -> bool:
    if _is_protected_event(event):
        return False
    sctx, ectx = _market_context(summary), _market_context(event)
    if str(sctx.get("market_date") or "") != str(ectx.get("market_date") or "") or _is_material_upgrade(event, summary):
        return False
    fact_key = _market_fact_key(event)
    if fact_key and fact_key in {str(x) for x in (sctx.get("covered_fact_keys") or [])}:
        return True
    code = str(event.get("security_code") or ectx.get("security_code") or "")
    name = str(event.get("security_name") or ectx.get("security_name") or "")
    text = f"{summary.get('title') or ''} {summary.get('content') or ''}"
    return bool(code and (code in text or (name and name in text))) and str(ectx.get("event_category") or "") == "REVERSAL" and any(word in text for word in ("修复", "反转", "V形", "回吐"))


def _find_recent_summary_absorption(items: list[dict], event: dict) -> dict | None:
    if _is_protected_event(event):
        return None
    stamp = parse_notification_time(event.get("created_at") or event.get("sent_at")) or now()
    for item in reversed(items):
        if str(item.get("event_type") or "") not in {"A_SHARE_SESSION_SUMMARY", "APAC_SESSION_SUMMARY", "US_SESSION_SUMMARY"}:
            continue
        prior_stamp = parse_notification_time(item.get("sent_at") or item.get("created_at"))
        if not prior_stamp or not (timedelta(0) <= stamp - prior_stamp <= timedelta(minutes=SUMMARY_ABSORB_MINUTES)):
            continue
        if _summary_covers_event(item, event):
            return item
    return None


def _find_aggregate_target(items: list[dict], event: dict) -> dict | None:
    if _is_protected_event(event):
        return None
    ctx = _market_context(event)
    category, code = str(ctx.get("event_category") or ""), str(event.get("security_code") or ctx.get("security_code") or "")
    direction, market_date = str(ctx.get("direction") or ""), str(ctx.get("market_date") or "")
    if category not in MERGEABLE_CATEGORIES or not code or not market_date:
        return None
    current_stamp = parse_notification_time(event.get("created_at")) or now()
    for item in reversed(items):
        if str(item.get("event_type") or "") not in MARKET_EVENT_TYPES or str(item.get("security_code") or "") != code:
            continue
        old = _market_context(item)
        if str(old.get("market_date") or "") != market_date or str(old.get("direction") or "") != direction:
            continue
        old_category = str(old.get("event_category") or "")
        if old_category == category or old_category not in MERGEABLE_CATEGORIES:
            continue
        prior_stamp = parse_notification_time(item.get("sent_at") or item.get("created_at"))
        if prior_stamp and timedelta(0) <= current_stamp - prior_stamp <= timedelta(minutes=AGGREGATION_WINDOW_MINUTES) and not _is_material_upgrade(event, item):
            return item
    return None


def _absorb_or_aggregate(notifications: list[dict], event: dict) -> tuple[str, dict] | None:
    summary = _find_recent_summary_absorption(notifications, event)
    if summary:
        return "ABSORBED_BY_SUMMARY", summary
    target = _find_aggregate_target(notifications, event)
    if target:
        ctx = dict(target.get("confirmation_context") or {})
        tags = list(ctx.get("event_tags") or [])
        for value in (ctx.get("event_category"), _market_context(event).get("event_category")):
            if value and value not in tags:
                tags.append(value)
        ctx["event_tags"] = tags
        ctx["aggregation_status"] = "MERGED_SAME_OBJECT_CONTINUOUS_FACT"
        target["confirmation_context"] = ctx
        return "AGGREGATED_INTO_EXISTING", target
    return None


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
    stale_error = _market_event_staleness_error(event)
    if stale_error:
        return {"status": "REJECTED_STALE_MARKET_EVENT", "detail": stale_error, "title": event.get("title")}
    event = _annotate_summary_increment(event, notifications)
    aggregate_result = _absorb_or_aggregate(notifications, event)
    if aggregate_result:
        status, target = aggregate_result
        if status == "AGGREGATED_INTO_EXISTING":
            stamp = now().isoformat(timespec="seconds")
            state.update({"schema_version": "2.2", "updated_at": stamp, "last_status": status, "last_type": target.get("event_type"), "last_title": target.get("title"), "notifications": notifications[-HISTORY_LIMIT:], "recent": [compact_recent(x) for x in notifications[-HISTORY_LIMIT:]], "pending_questions": [x["notification_id"] for x in notifications if x.get("lifecycle_status") == "WAITING_CONFIRMATION"], "policy": policy})
            write_json(NOTIFICATION_STATE, state)
        return {"status": status, "notification_id": target.get("notification_id"), "event_tags": (target.get("confirmation_context") or {}).get("event_tags", [])}
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


