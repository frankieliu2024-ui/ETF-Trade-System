from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from market_notification_common import number, pct, persist_and_send, render_summary
from notification_center import STATE, now, read_json

CONTEXT = STATE / "us_extended_hours_context.json"
NEW_YORK = ZoneInfo("America/New_York")


def _market_tone(qqq: float | None, soxx: float | None) -> tuple[str, str]:
    values = [x for x in (qqq, soxx) if x is not None]
    if len(values) < 2:
        return "信息不足", "两项核心代理未同时取得有效数据，本次不把不完整信息解释为明确海外结构。"
    if qqq * soxx < 0 and abs(qqq - soxx) >= 0.75:
        return "明显分化", "纳指100与半导体方向分化；下一A股交易节点需要分别验证科技风险偏好与半导体链条反馈，不能把单一方向机械映射到A股科技ETF。"
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
    qqq_change = number(qqq.get("regular_session_change_vs_previous_close_pct"))
    soxx_change = number(soxx.get("regular_session_change_vs_previous_close_pct"))
    if qqq_change is None or soxx_change is None:
        return None
    tone, implication = _market_tone(qqq_change, soxx_change)
    latest_times = [str((qqq.get("latest") or {}).get("as_of_beijing") or ""), str((soxx.get("latest") or {}).get("as_of_beijing") or "")]
    as_of = max([x for x in latest_times if x], default=str(context.get("generated_at_beijing") or ""))
    generated = now().strftime("%Y-%m-%d %H:%M:%S")

    if node == "OPEN":
        title = f"【美股开盘｜次日A股参考】科技风险偏好{tone}"
        node_name = "美股现金盘开盘后稳定观察窗口"
        action = "今晚无需机械调整A股持仓；把开盘结构作为海外证据保存，继续观察其是否在美股日内强化、衰减或反转。"
        boundary = "开盘总结不是全天结论；QQQ/SOXX是科技与半导体代理，不等同于纳斯达克100指数（NDX）/费城半导体指数（SOX）的扩展时段指数报价。"
    else:
        title = f"【美股收盘｜次日A股参考】科技风险偏好{tone}"
        node_name = "美股现金盘收盘总结"
        action = "将收盘方向和日内路径直接纳入下一A股交易日盘前分析，再验证本地指数、目标ETF自身反馈与资本效率。"
        boundary = "收盘总结只提供海外结构证据；盘后变化仍属于扩展时段前置信号。海外结构必须继续经过本地传导和ETF自身反馈后才能进入机会判断。"

    content = render_summary(
        headline_lines=[
            f"- **节点**：{node_name}",
            f"- **纳指100ETF代理（QQQ）**：较上一现金盘收盘 {pct(qqq_change)}；较当日开盘 {pct(number(qqq.get('regular_session_change_from_open_pct')))}",
            f"- **半导体ETF代理（SOXX）**：较上一现金盘收盘 {pct(soxx_change)}；较当日开盘 {pct(number(soxx.get('regular_session_change_from_open_pct')))}",
            f"- **综合判断**：{tone}",
        ],
        path_lines=[_path_line("纳指100ETF代理（QQQ）", qqq), _path_line("半导体ETF代理（SOXX）", soxx)],
        implication=implication,
        action=action,
        as_of_lines=[f"- **行情依据**：{as_of or '未提供'}", f"- **通知生成**：{generated}"],
        boundary=boundary + "\n\n> 海外结构 → 本地传导 → ETF自身反馈 → 机会判断。",
    )
    return {
        "key": f"us-session-summary:{qqq_date}:{node}",
        "type": "美股节点总结",
        "event_type": "US_SESSION_SUMMARY",
        "title": title,
        "content": content,
        "source": "us_extended_hours_context",
        "user_severity": "需要关注",
        "user_action": "纳入下一A股交易节点的海外结构判断，无需机械交易",
        "confirmation_context": {"us_market_date": qqq_date, "session_node": node, "qqq_change_pct": qqq_change, "soxx_change_pct": soxx_change, "tone": tone, "market_as_of_beijing": as_of},
    }


def main() -> int:
    event = build_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(event, policy="节点总结采用统一模板并包含日内路径；美股开盘/收盘各一次，作为下一A股交易节点的海外结构证据，不直接生成A股动作。")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
