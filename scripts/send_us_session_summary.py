from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from market_notification_common import number, pct, pct_change, persist_and_send, render_summary
from notification_center import STATE, now, read_json

CONTEXT = STATE / "us_extended_hours_context.json"
NEW_YORK = ZoneInfo("America/New_York")
US_OPEN_ABS_PCT = 1.0
US_OPEN_DIVERGENCE_PCT = 1.0


def _market_tone(tech: float | None, semi: float | None) -> tuple[str, str]:
    values = [x for x in (tech, semi) if x is not None]
    if len(values) < 2:
        return "信息不足", "两项核心美股科技指标未同时取得有效数据，本次不把不完整信息解释为明确海外结构。"
    if tech * semi < 0 and abs(tech - semi) >= 0.75:
        return "明显分化", "纳斯达克100与半导体方向分化；下一A股交易节点需要分别验证科技风险偏好与半导体链条反馈，不能把单一方向机械映射到A股科技ETF。"
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
    # Keep the actual opening fact recoverable through the first cash-session
    # hour. This tolerates a delayed/missed first GitHub schedule without turning
    # a material opening gap into NO_NOTIFICATION_NEEDED after an early fade.
    if phase == "REGULAR" and 9 * 60 + 35 <= minute <= 10 * 60 + 30:
        return "OPEN"
    if phase == "POST_MARKET" and 16 * 60 + 5 <= minute <= 16 * 60 + 30:
        return "CLOSE"
    return ""


def _opening_gap(obj: dict) -> float | None:
    return pct_change(number(obj.get("regular_session_open_reference")), number(obj.get("previous_regular_close_reference")))


def _valuable_open(
    tech_open_gap: float | None,
    semi_open_gap: float | None,
    tech_current: float,
    semi_current: float,
) -> tuple[bool, str]:
    opening_values = [x for x in (tech_open_gap, semi_open_gap) if x is not None]
    max_open_abs = max((abs(x) for x in opening_values), default=0.0)
    open_divergence = abs(tech_open_gap - semi_open_gap) if tech_open_gap is not None and semi_open_gap is not None else 0.0
    current_max_abs = max(abs(tech_current), abs(semi_current))
    current_divergence = abs(tech_current - semi_current)
    if max_open_abs >= US_OPEN_ABS_PCT:
        return True, f"实际现金盘开盘相对前收最大跳空约{max_open_abs:.2f}%"
    if open_divergence >= US_OPEN_DIVERGENCE_PCT:
        return True, f"两项核心美股科技指标实际开盘跳空分化约{open_divergence:.2f}个百分点"
    if current_max_abs >= US_OPEN_ABS_PCT:
        return True, f"开盘观察窗口当前最大绝对涨跌约{current_max_abs:.2f}%"
    if current_divergence >= US_OPEN_DIVERGENCE_PCT:
        return True, f"两项核心美股科技指标开盘观察窗口分化约{current_divergence:.2f}个百分点"
    return False, "实际开盘跳空及开盘观察窗口结构均未达到主动通知价值门槛"


def _path_line(label: str, obj: dict) -> str:
    path = obj.get("regular_session_path") or {}
    if not path:
        return f"- **{label}**：日内路径数据暂不完整。"
    first_hour = number(path.get("first_hour_change_pct"))
    midday = number(path.get("midday_change_pct"))
    late = number(path.get("late_session_change_pct"))
    range_pos = number(path.get("range_position"))
    pos_text = ""
    if range_pos is not None:
        pos_text = "，收/现价位于日内区间高位" if range_pos >= 0.8 else ("，收/现价位于日内区间低位" if range_pos <= 0.2 else "，收/现价位于日内区间中部")
    segment_text = f"首小时{pct(first_hour)}、中段{pct(midday)}、尾段{pct(late)}"
    return (
        f"- **{label}**：高点{path.get('high','数据不可用')}、低点{path.get('low','数据不可用')}；"
        f"自低点修复{pct(number(path.get('recovery_from_low_pct')))}，距高点{pct(number(path.get('retreat_from_high_pct')))}；"
        f"{segment_text}{pos_text}。"
    )


def _open_path_phrase(label: str, obj: dict, open_gap: float | None) -> str:
    current_from_open = number(obj.get("regular_session_change_from_open_pct"))
    path = obj.get("regular_session_path") or {}
    high = number(path.get("high"))
    prev_close = number(obj.get("previous_regular_close_reference"))
    high_vs_prev = pct_change(high, prev_close)
    return (
        f"- **{label}**：实际开盘较前收{pct(open_gap)}；"
        f"开盘至当前{pct(current_from_open)}；"
        f"开盘后阶段高点较前收{pct(high_vs_prev)}。"
    )


def _usable(obj: dict) -> bool:
    return bool(obj) and obj.get("quality_status") in {"PASS", "FRESH"}


def _select_cash_pair(objects: dict) -> tuple[dict, dict, str, str, bool] | None:
    ndx = objects.get("NDX") or {}
    sox = objects.get("SOX") or {}
    if _usable(ndx) and _usable(sox):
        return ndx, sox, "纳斯达克100指数（NDX）", "费城半导体指数（SOX）", True
    qqq = objects.get("QQQ") or {}
    soxx = objects.get("SOXX") or {}
    if _usable(qqq) and _usable(soxx):
        return qqq, soxx, "纳指100ETF代理（QQQ）", "半导体ETF代理（SOXX）", False
    return None


def build_event() -> dict | None:
    context = read_json(CONTEXT, {})
    objects = context.get("objects") or {}
    selected = _select_cash_pair(objects)
    if not selected:
        return None
    tech, semi, tech_label, semi_label, direct = selected

    now_et = datetime.now(timezone.utc).astimezone(NEW_YORK)
    phase = str(tech.get("current_market_phase") or "")
    if phase != str(semi.get("current_market_phase") or ""):
        return None
    node = _window(now_et, phase)
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
    tech_open_gap = _opening_gap(tech)
    semi_open_gap = _opening_gap(semi)

    value_reason = "固定收盘总结"
    if node == "OPEN":
        valuable, value_reason = _valuable_open(tech_open_gap, semi_open_gap, tech_change, semi_change)
        if not valuable:
            return None

    tone, implication = _market_tone(tech_change, semi_change)
    latest_times = [str((tech.get("latest") or {}).get("as_of_beijing") or ""), str((semi.get("latest") or {}).get("as_of_beijing") or "")]
    as_of = max([x for x in latest_times if x], default=str(context.get("generated_at_beijing") or ""))
    generated = now().strftime("%Y-%m-%d %H:%M:%S")
    data_basis = "正式现金指数直接行情" if direct else "直接指数当前不可用，使用明确ETF代理备份"

    if node == "OPEN":
        lead_is_semi = abs(semi_open_gap or 0.0) >= abs(tech_open_gap or 0.0)
        lead_label = semi_label if lead_is_semi else tech_label
        lead_gap = semi_open_gap if lead_is_semi else tech_open_gap
        lead_obj = semi if lead_is_semi else tech
        lead_from_open = number(lead_obj.get("regular_session_change_from_open_pct"))
        if lead_gap is not None and lead_gap >= US_OPEN_ABS_PCT and lead_from_open is not None and lead_from_open <= -0.75:
            title = f"【异动提醒】{lead_label}高开{pct(lead_gap)}后快速回落"
        elif lead_gap is not None and lead_gap <= -US_OPEN_ABS_PCT and lead_from_open is not None and lead_from_open >= 0.75:
            title = f"【异动提醒】{lead_label}低开{pct(lead_gap)}后快速修复"
        else:
            title = "【异动提醒】美股开盘结构出现有价值变化"
        node_name = "美股现金盘开盘后的有价值结构信号"
        action = "今晚无需机械调整A股持仓；把实际开盘跳空和开盘后强化/衰减路径作为海外证据保存，并继续观察其在日内是否确认或反转。"
        boundary = (
            "REGULAR优先使用纳斯达克100指数（NDX）和费城半导体指数（SOX）直接指数行情；"
            "只有直接指数不可用时才明确降级到QQQ/SOXX代理。实际开盘跳空在首小时恢复窗口内持续作为事实，"
            "不能因GitHub首个成功pulse延迟、价格随后回落而被抹掉。门槛只控制注意力。"
        )
        path_lines = [_open_path_phrase(tech_label, tech, tech_open_gap), _open_path_phrase(semi_label, semi, semi_open_gap)]
    else:
        title = f"【收盘总结】美股科技风险偏好{tone}"
        node_name = "美股现金盘固定收盘总结"
        action = "将收盘方向和日内路径直接纳入下一A股交易日盘前分析，再验证本地指数、目标ETF自身反馈与资本效率。"
        boundary = (
            "现金盘总结优先使用纳斯达克100指数（NDX）和费城半导体指数（SOX）直接指数行情；"
            "盘后变化仍属于扩展时段前置信号，使用QQQ/SOXX等可交易代理。海外结构必须继续经过本地传导和ETF自身反馈后才能进入机会判断。"
        )
        path_lines = [_path_line(tech_label, tech), _path_line(semi_label, semi)]

    content = render_summary(
        headline_lines=[
            f"- **节点**：{node_name}",
            f"- **数据口径**：{data_basis}",
            f"- **通知价值**：{value_reason}",
            f"- **{tech_label}**：实际开盘较前收 {pct(tech_open_gap)}；当前较前收 {pct(tech_change)}；较当日开盘 {pct(number(tech.get('regular_session_change_from_open_pct')))}",
            f"- **{semi_label}**：实际开盘较前收 {pct(semi_open_gap)}；当前较前收 {pct(semi_change)}；较当日开盘 {pct(number(semi.get('regular_session_change_from_open_pct')))}",
            f"- **综合判断**：{tone}",
        ],
        path_lines=path_lines,
        implication=implication,
        action=action,
        as_of_lines=[f"- **行情依据**：{as_of or '未提供'}", f"- **通知生成**：{generated}"],
        boundary=boundary + "\n\n> 海外结构 → 本地传导 → ETF自身反馈 → 机会判断。",
    )
    return {
        "key": f"us-session-summary:{tech_date}:{node}",
        "type": "美股市场总结" if node == "CLOSE" else "市场有价值事件",
        "event_type": "US_SESSION_SUMMARY" if node == "CLOSE" else "US_OPEN_VALUE_ALERT",
        "title": title,
        "content": content,
        "source": "us_extended_hours_context",
        "user_severity": "需要关注",
        "user_action": "纳入下一A股交易节点的海外结构判断，无需机械交易",
        "confirmation_context": {
            "us_market_date": tech_date,
            "session_node": node,
            "direct_cash_indices_used": direct,
            "tech_code": "NDX" if direct else "QQQ",
            "semi_code": "SOX" if direct else "SOXX",
            "tech_open_gap_pct": tech_open_gap,
            "semi_open_gap_pct": semi_open_gap,
            "tech_change_pct": tech_change,
            "semi_change_pct": semi_change,
            "tone": tone,
            "market_as_of_beijing": as_of,
            "value_reason": value_reason,
        },
    }


def main() -> int:
    event = build_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(
        event,
        policy="美股现金盘优先使用NDX/SOX直接指数；QQQ/SOXX只在直接指数不可用或PRE/POST扩展时段承担明确代理角色。开盘有价值事实在首小时恢复窗口内持续保留，现金盘收盘固定总结，不直接生成A股动作。",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
