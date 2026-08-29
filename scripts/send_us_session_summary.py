from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from market_notification_common import number, pct, pct_change, persist_and_send, render_summary
from notification_center import STATE, now, parse_notification_time, read_json
from session_notification_semantics import us_session_structure

CONTEXT = STATE / "us_extended_hours_context.json"
NEW_YORK = ZoneInfo("America/New_York")
US_OPEN_ABS_PCT = 1.0
US_OPEN_DIVERGENCE_PCT = 1.0
US_INDEX_EXTREME_PCT = 1.5
MAX_FUTURE_SKEW_SECONDS = 120


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


def _regular_cash_close_beijing(market_date: str) -> str:
    try:
        local_close = datetime.fromisoformat(f"{market_date}T16:00:00").replace(tzinfo=NEW_YORK)
    except ValueError:
        return "未提供"
    return local_close.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _open_focus(tech_label: str, semi_label: str, tech_gap: float | None, semi_gap: float | None, tech_change: float, semi_change: float) -> tuple[str, float | None, float]:
    candidates = [
        (tech_label, tech_gap, tech_change),
        (semi_label, semi_gap, semi_change),
    ]
    return max(candidates, key=lambda x: max(abs(x[1] or 0.0), abs(x[2])))


def _open_title(tech_label: str, semi_label: str, tech_gap: float | None, semi_gap: float | None, tech: dict, semi: dict) -> str:
    tech_change = number(tech.get("regular_session_change_vs_previous_close_pct")) or 0.0
    semi_change = number(semi.get("regular_session_change_vs_previous_close_pct")) or 0.0
    label, gap, current = _open_focus(tech_label, semi_label, tech_gap, semi_gap, tech_change, semi_change)
    obj = tech if label == tech_label else semi
    from_open = number(obj.get("regular_session_change_from_open_pct"))

    if gap is not None and abs(gap) >= US_OPEN_ABS_PCT and abs(current) >= US_INDEX_EXTREME_PCT:
        verb = "低开" if gap < 0 else "高开"
        return f"【异动提醒】{label}{verb}{pct(gap)}，当前{pct(current)}"
    if gap is not None and gap >= US_OPEN_ABS_PCT:
        if from_open is not None and from_open <= -0.35:
            return f"【异动提醒】{label}高开{pct(gap)}后明显回吐"
        return f"【异动提醒】{label}高开{pct(gap)}"
    if gap is not None and gap <= -US_OPEN_ABS_PCT:
        if from_open is not None and from_open >= 0.35:
            return f"【异动提醒】{label}低开{pct(gap)}后明显修复"
        return f"【异动提醒】{label}低开{pct(gap)}"
    return "【异动提醒】美股科技指数开盘出现显著分化"


def _open_fact_lines(tech_label: str, semi_label: str, tech_gap: float | None, semi_gap: float | None, tech_change: float, semi_change: float, value_reason: str) -> list[str]:
    lines = [f"- **触发原因**：{value_reason}。"]
    for label, gap, current in ((tech_label, tech_gap, tech_change), (semi_label, semi_gap, semi_change)):
        if gap is not None and abs(gap) >= US_OPEN_ABS_PCT:
            lines.append(f"- **开盘事实｜{label}**：实际现金盘开盘相对前收{pct(gap)}。")
        if abs(current) >= US_INDEX_EXTREME_PCT:
            lines.append(f"- **当前进展｜{label}**：相对前收{pct(current)}，已进入极端波动关注区间。")
    return lines


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
        valuable, value_reason = _valuable_open(tech_gap, semi_gap, tech_change, semi_change)
        if not valuable:
            return None
    else:
        value_reason = "固定现金盘收盘总结"

    latest_times = [str((tech.get("latest") or {}).get("as_of_beijing") or ""), str((semi.get("latest") or {}).get("as_of_beijing") or "")]
    as_of, time_ok = _as_of_guard(latest_times)
    if not time_ok:
        print(json.dumps({"status": "INVALID_DATA_TIME", "latest_times": latest_times, "generated_at": now().isoformat(timespec="seconds")}, ensure_ascii=False))
        return None

    headline, path_lines, implication, action, tone = us_session_structure(
        tech=tech,
        semi=semi,
        tech_label=tech_label,
        semi_label=semi_label,
        direct=direct,
        node=node,
    )
    if node == "OPEN":
        title = _open_title(tech_label, semi_label, tech_gap, semi_gap, tech, semi)
        headline = _open_fact_lines(tech_label, semi_label, tech_gap, semi_gap, tech_change, semi_change, value_reason) + headline
        boundary = "这是海外结构证据，不是A股买卖信号；实际现金盘开盘事实可在同一常规交易时段恢复，但不得把后续累计涨跌伪装成开盘跳空。"
        as_of_lines = [
            f"- **行情时点（北京时间）**：{as_of}",
            f"- **通知生成（北京时间）**：{now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
    else:
        title = f"【收盘总结】美股科技结构{tone}"
        boundary = "美股常规交易时段（现金盘）优先使用纳斯达克100指数（NDX）和费城半导体指数（SOX）直接指数；盘前、盘后仅作前置信号。海外结构必须经过A股本地传导和ETF自身反馈后才能进入机会判断。"
        as_of_lines = [
            f"- **美股现金盘收盘时点（北京时间）**：{_regular_cash_close_beijing(tech_date)}",
            f"- **本次复核数据时点（北京时间）**：{as_of}",
            f"- **通知生成（北京时间）**：{now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

    content = render_summary(
        headline_lines=headline,
        path_lines=path_lines,
        implication=implication,
        action=action,
        as_of_lines=as_of_lines,
        boundary=boundary,
    )
    return {
        "key": f"us-session-summary:{tech_date}:{node}",
        "type": "美股市场总结" if node == "CLOSE" else "市场有价值事件",
        "event_type": "US_SESSION_SUMMARY" if node == "CLOSE" else "US_OPEN_VALUE_ALERT",
        "title": title,
        "content": content,
        "source": "us_extended_hours_context",
        "user_severity": "需要关注",
        "user_action": "纳入下一A股交易节点复核，无需机械交易",
        "confirmation_context": {
            "us_market_date": tech_date,
            "session_node": node,
            "direct_cash_indices_used": direct,
            "tech_open_gap_pct": tech_gap,
            "semi_open_gap_pct": semi_gap,
            "tech_change_pct": tech_change,
            "semi_change_pct": semi_change,
            "market_as_of_beijing": as_of,
            "cash_close_as_of_beijing": _regular_cash_close_beijing(tech_date) if node == "CLOSE" else "",
            "generated_at_beijing": now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


def main() -> int:
    event = build_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(
        event,
        policy="US session summary reports material opening structure and fixed cash close; market-specific data routing may differ, but user-visible semantics stay aligned with the unified ETF notification system.",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
