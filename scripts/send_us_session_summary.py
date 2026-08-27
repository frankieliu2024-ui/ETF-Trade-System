from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from market_notification_common import number, pct, pct_change, persist_and_send, render_summary
from notification_center import STATE, now, parse_notification_time, read_json

CONTEXT = STATE / "us_extended_hours_context.json"
NEW_YORK = ZoneInfo("America/New_York")
US_OPEN_ABS_PCT = 1.0
US_OPEN_DIVERGENCE_PCT = 1.0
MAX_FUTURE_SKEW_SECONDS = 120


def _market_tone(tech: float | None, semi: float | None) -> tuple[str, str]:
    values = [x for x in (tech, semi) if x is not None]
    if len(values) < 2:
        return "信息不足", "两项核心美股科技指标未同时取得有效数据，本次不形成明确海外结构判断。"
    spread = semi - tech
    if tech * semi < 0 and abs(spread) >= 0.75:
        return "明显分化", "纳斯达克100与半导体方向分化；下一A股交易节点需分别验证科技风险偏好与半导体链反馈。"
    avg = sum(values) / len(values)
    if avg >= 1.5:
        return "明显偏强", "外部科技风险偏好明显增强；下一A股交易节点优先验证科创、半导体、通信方向是否出现同向本地反馈。"
    if avg >= 0.5:
        return "偏强", "外部科技结构偏强；下一A股交易节点重点验证科技方向，但仍以A股自身反馈为准。"
    if avg <= -1.5:
        return "明显偏弱", "外部科技风险偏好明显走弱；下一A股交易节点优先复核高相关科技篮子的承接与风险收益。"
    if avg <= -0.5:
        return "偏弱", "外部科技结构偏弱；下一A股交易节点提高对科技持仓承接与相对强弱的关注。"
    return "相对平稳", "外部科技结构尚不足以单独改变A股交易判断，作为下一A股节点背景证据保存。"


def _window(now_et: datetime, phase: str) -> str:
    minute = now_et.hour * 60 + now_et.minute
    if phase == "REGULAR" and minute >= 9 * 60 + 35:
        return "OPEN"
    if phase == "POST_MARKET" and minute >= 16 * 60 + 5:
        return "CLOSE"
    return ""


def _opening_gap(obj: dict) -> float | None:
    return pct_change(number(obj.get("regular_session_open_reference")), number(obj.get("previous_regular_close_reference")))


def _valuable_open(tech_gap: float | None, semi_gap: float | None, tech_current: float, semi_current: float) -> tuple[bool, str]:
    gaps = [x for x in (tech_gap, semi_gap) if x is not None]
    max_gap = max((abs(x) for x in gaps), default=0.0)
    gap_spread = abs(tech_gap - semi_gap) if tech_gap is not None and semi_gap is not None else 0.0
    current_max = max(abs(tech_current), abs(semi_current))
    current_spread = abs(tech_current - semi_current)
    if max_gap >= US_OPEN_ABS_PCT:
        return True, f"实际现金盘开盘最大跳空{max_gap:.2f}%"
    if gap_spread >= US_OPEN_DIVERGENCE_PCT:
        return True, f"两项核心科技指数开盘分化{gap_spread:.2f}个百分点"
    if current_max >= US_OPEN_ABS_PCT:
        return True, f"现金盘当前最大绝对涨跌{current_max:.2f}%"
    if current_spread >= US_OPEN_DIVERGENCE_PCT:
        return True, f"两项核心科技指数当前分化{current_spread:.2f}个百分点"
    return False, "实际开盘及当前结构均未达到主动通知价值门槛"


def _usable(obj: dict) -> bool:
    return bool(obj) and obj.get("quality_status") in {"PASS", "FRESH"}


def _select_cash_pair(objects: dict) -> tuple[dict, dict, str, str, bool] | None:
    ndx, sox = objects.get("NDX") or {}, objects.get("SOX") or {}
    if _usable(ndx) and _usable(sox):
        return ndx, sox, "纳斯达克100指数（NDX）", "费城半导体指数（SOX）", True
    qqq, soxx = objects.get("QQQ") or {}, objects.get("SOXX") or {}
    if _usable(qqq) and _usable(soxx):
        return qqq, soxx, "纳指100ETF代理（QQQ）", "半导体ETF代理（SOXX）", False
    return None


def _as_of_guard(values: list[str]) -> tuple[str, bool]:
    parsed = [(v, parse_notification_time(v)) for v in values if v]
    parsed = [(v, dt) for v, dt in parsed if dt]
    if not parsed:
        return "", False
    value, dt = max(parsed, key=lambda x: x[1])
    return value, (dt - now()).total_seconds() <= MAX_FUTURE_SKEW_SECONDS


def _path_numbers(obj: dict) -> tuple[float | None, float | None, float | None]:
    path = obj.get("regular_session_path") or {}
    prev = number(obj.get("previous_regular_close_reference"))
    return pct_change(number(path.get("high")), prev), pct_change(number(path.get("low")), prev), number(obj.get("regular_session_change_from_open_pct"))


def _open_line(label: str, obj: dict, gap: float | None, current: float | None) -> str:
    high_vs_prev, low_vs_prev, from_open = _path_numbers(obj)
    return f"- **{label}**：开盘{pct(gap)}；当前{pct(current)}；开盘后{pct(from_open)}；日内最高{pct(high_vs_prev)}、最低{pct(low_vs_prev)}（均相对前收）。"


def _close_line(label: str, obj: dict, current: float | None) -> str:
    path = obj.get("regular_session_path") or {}
    return f"- **{label}**：收盘{pct(current)}；首小时{pct(number(path.get('first_hour_change_pct')))}；中段{pct(number(path.get('midday_change_pct')))}；尾段{pct(number(path.get('late_session_change_pct')))}；距日内高点{pct(number(path.get('retreat_from_high_pct')))}。"


def _open_title(tech_label: str, semi_label: str, tech_gap: float | None, semi_gap: float | None, tech: dict, semi: dict) -> str:
    label, gap, obj = max([(tech_label, tech_gap, tech), (semi_label, semi_gap, semi)], key=lambda x: abs(x[1] or 0.0))
    from_open = number(obj.get("regular_session_change_from_open_pct"))
    if gap is not None and gap >= US_OPEN_ABS_PCT:
        if from_open is not None and from_open <= -0.35:
            return f"【异动提醒】{label}高开{pct(gap)}后回吐{pct(from_open)}"
        return f"【异动提醒】{label}高开{pct(gap)}"
    if gap is not None and gap <= -US_OPEN_ABS_PCT:
        if from_open is not None and from_open >= 0.35:
            return f"【异动提醒】{label}低开{pct(gap)}后修复{pct(from_open)}"
        return f"【异动提醒】{label}低开{pct(gap)}"
    return "【异动提醒】美股科技指数开盘出现显著分化"


def build_event() -> dict | None:
    context = read_json(CONTEXT, {})
    selected = _select_cash_pair(context.get("objects") or {})
    if not selected:
        return None
    tech, semi, tech_label, semi_label, direct = selected
    phase = str(tech.get("current_market_phase") or "")
    if phase != str(semi.get("current_market_phase") or ""):
        return None
    node = _window(datetime.now(timezone.utc).astimezone(NEW_YORK), phase)
    if not node:
        return None
    tech_date = str(tech.get("regular_session_market_date") or (tech.get("latest") or {}).get("market_date_local") or "")
    semi_date = str(semi.get("regular_session_market_date") or (semi.get("latest") or {}).get("market_date_local") or "")
    if not tech_date or tech_date != semi_date:
        return None
    tech_change = number(tech.get("regular_session_change_vs_previous_close_pct"))
    semi_change = number(semi.get("regular_session_change_vs_previous_close_pct"))
    if tech_change is None or semi_change is None:
        return None
    tech_gap, semi_gap = _opening_gap(tech), _opening_gap(semi)
    if node == "OPEN":
        valuable, reason = _valuable_open(tech_gap, semi_gap, tech_change, semi_change)
        if not valuable:
            return None
    else:
        reason = "固定现金盘收盘总结"
    latest_times = [str((tech.get("latest") or {}).get("as_of_beijing") or ""), str((semi.get("latest") or {}).get("as_of_beijing") or "")]
    as_of, time_ok = _as_of_guard(latest_times)
    if not time_ok:
        print(json.dumps({"status": "INVALID_DATA_TIME", "latest_times": latest_times, "generated_at": now().isoformat(timespec="seconds")}, ensure_ascii=False))
        return None
    tone, implication = _market_tone(tech_change, semi_change)
    generated = now().strftime("%Y-%m-%d %H:%M:%S")
    basis = "NDX/SOX直接指数" if direct else "直接指数不可用，降级为QQQ/SOXX代理"
    if node == "OPEN":
        title = _open_title(tech_label, semi_label, tech_gap, semi_gap, tech, semi)
        _, _, tech_from_open = _path_numbers(tech)
        _, _, semi_from_open = _path_numbers(semi)
        fade_text = ""
        if semi_gap is not None and semi_gap >= 1.0 and semi_from_open is not None and semi_from_open < 0:
            fade_text = f"半导体高开后已回吐{abs(semi_from_open):.2f}个百分点，强度较开盘减弱。"
        elif tech_gap is not None and tech_gap >= 1.0 and tech_from_open is not None and tech_from_open < 0:
            fade_text = f"科技指数高开后已回吐{abs(tech_from_open):.2f}个百分点，强度较开盘减弱。"
        implication = (fade_text + implication).strip()
        action = "作为下一A股交易节点的海外证据保存；重点验证半导体设备ETF（561980）、通信ETF（515880）及科创方向是否出现同向本地反馈，不因海外单一信号机械交易。"
        path_lines = [_open_line(tech_label, tech, tech_gap, tech_change), _open_line(semi_label, semi, semi_gap, semi_change)]
        boundary = "这是海外结构证据，不是A股买卖信号。实际开盘事实可在现金盘内补发，避免因调度延迟或随后回落而永久遗漏。"
    else:
        title = f"【收盘总结】美股科技结构{tone}"
        action = "纳入下一A股盘前：先看本地传导，再看目标ETF自身反馈与资本效率。"
        path_lines = [_close_line(tech_label, tech, tech_change), _close_line(semi_label, semi, semi_change)]
        boundary = "REGULAR优先使用NDX/SOX直接指数；PRE/POST仅作前置信号。海外结构必须经过本地传导和ETF自身反馈后才能进入机会判断。"
    content = render_summary(
        headline_lines=[f"- **核心事件**：{reason}", f"- **数据口径**：{basis}", f"- **{tech_label}**：开盘{pct(tech_gap)}，当前{pct(tech_change)}，较开盘{pct(number(tech.get('regular_session_change_from_open_pct')))}", f"- **{semi_label}**：开盘{pct(semi_gap)}，当前{pct(semi_change)}，较开盘{pct(number(semi.get('regular_session_change_from_open_pct')))}", f"- **当前结构**：{tone}"],
        path_lines=path_lines,
        implication=implication,
        action=action,
        as_of_lines=[f"- **行情依据**：{as_of}", f"- **通知生成**：{generated}"],
        boundary=boundary + "\n\n> 海外结构 → 本地传导 → ETF自身反馈 → 机会判断。",
    )
    return {"key": f"us-session-summary:{tech_date}:{node}", "type": "美股市场总结" if node == "CLOSE" else "市场有价值事件", "event_type": "US_SESSION_SUMMARY" if node == "CLOSE" else "US_OPEN_VALUE_ALERT", "title": title, "content": content, "source": "us_extended_hours_context", "user_severity": "需要关注", "user_action": "纳入下一A股交易节点的海外结构判断，无需机械交易", "confirmation_context": {"us_market_date": tech_date, "session_node": node, "direct_cash_indices_used": direct, "tech_open_gap_pct": tech_gap, "semi_open_gap_pct": semi_gap, "tech_change_pct": tech_change, "semi_change_pct": semi_change, "market_as_of_beijing": as_of, "generated_at_beijing": generated}}


def main() -> int:
    event = build_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(event, policy="US session summary reports material opening structure and fixed cash close; opening facts remain recoverable after scheduler delay, timestamps must not point materially into the future, and no notification creates an A-share trade action.")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
