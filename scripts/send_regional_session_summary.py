from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from market_notification_common import number, ohlc_path_phrase, pct, pct_change, persist_and_send, render_summary
from notification_center import STATE, now, read_json

ROOT = Path(__file__).resolve().parents[1]
BEIJING = ZoneInfo("Asia/Shanghai")
CURRENT = STATE / "CURRENT.json"
PATH_FEATURES = STATE / "intraday_path_features.json"
OVERSEAS = STATE / "overseas_context.json"
NOTIFICATION_STATE = STATE / "notification_center.json"

# Open-event thresholds remain somewhat higher than generic intraday-event
# thresholds because opening prints are noisier. They are attention gates only.
A_SHARE_OPEN_INDEX_ABS_PCT = 0.8
A_SHARE_OPEN_ETF_ABS_PCT = 1.5
A_SHARE_OPEN_ETF_SPREAD_PCT = 2.0
APAC_OPEN_SIGNAL_ABS_PCT = 0.8
APAC_LATE_HK_CHANGE_PCT = 0.8

APAC_SPECS = [
    ("N225", "日经225指数（N225）"),
    ("KOSPI", "韩国综合指数（KOSPI）"),
    ("TWII", "台湾加权指数（TWII）"),
    ("HSTECH", "恒生科技指数（HSTECH）"),
]

# Only explicit transmission relationships are named in user-facing APAC alerts.
# The mapping supplies context, not trading permission.
APAC_DIRECT_ETF = {
    "N225": ("513520", "日经ETF（513520）"),
    "HSTECH": ("513180", "恒生科技ETF（513180）"),
}


def _minute_now() -> int:
    dt = datetime.now(BEIJING)
    return dt.hour * 60 + dt.minute


def _feature_map() -> dict[str, dict]:
    data = read_json(PATH_FEATURES, {})
    return {str(x.get("symbol") or ""): x for x in (data.get("features") or [])}


def _a_share_node() -> str:
    minute = _minute_now()
    # Opening facts remain recoverable throughout the morning session. A close
    # summary remains recoverable after 15:00 on the same market date whenever
    # CURRENT still points to a verified formal close. Event keys make this
    # idempotent, so delayed GitHub schedules cannot permanently erase facts.
    if 9 * 60 + 35 <= minute <= 11 * 60 + 30:
        return "OPEN"
    if minute >= 15 * 60:
        return "CLOSE"
    return ""


def _derived_previous_close(row: dict) -> float | None:
    close = number(row.get("close"))
    change = number(row.get("change_pct"))
    if close is None or change is None or change <= -99.9:
        return None
    return close / (1.0 + change / 100.0)


def _a_share_open_gap(row: dict) -> float | None:
    return pct_change(number(row.get("open")), _derived_previous_close(row))


def _a_share_open_is_valuable(indices: dict[str, dict], etfs: list[dict]) -> tuple[bool, str]:
    index_open = [abs(x) for x in (_a_share_open_gap(r) for r in indices.values()) if x is not None]
    etf_open_signed = [x for x in (_a_share_open_gap(r) for r in etfs) if x is not None]
    current_index = [abs(float(x.get("change_pct"))) for x in indices.values() if number(x.get("change_pct")) is not None]
    current_etf = [float(x.get("change_pct")) for x in etfs if number(x.get("change_pct")) is not None]
    max_index_open = max(index_open, default=0.0)
    max_etf_open_abs = max((abs(x) for x in etf_open_signed), default=0.0)
    open_spread = (max(etf_open_signed) - min(etf_open_signed)) if etf_open_signed else 0.0
    current_max_index = max(current_index, default=0.0)
    current_max_etf_abs = max((abs(x) for x in current_etf), default=0.0)
    current_spread = (max(current_etf) - min(current_etf)) if current_etf else 0.0
    if max_index_open >= A_SHARE_OPEN_INDEX_ABS_PCT:
        return True, f"核心指数实际开盘相对前收最大跳空约{max_index_open:.2f}%"
    if max_etf_open_abs >= A_SHARE_OPEN_ETF_ABS_PCT:
        return True, f"监测ETF实际开盘最大绝对跳空约{max_etf_open_abs:.2f}%"
    if open_spread >= A_SHARE_OPEN_ETF_SPREAD_PCT:
        return True, f"监测ETF实际开盘横截面分化约{open_spread:.2f}个百分点"
    if current_max_index >= A_SHARE_OPEN_INDEX_ABS_PCT:
        return True, f"上午观察窗口核心指数最大绝对涨跌约{current_max_index:.2f}%"
    if current_max_etf_abs >= A_SHARE_OPEN_ETF_ABS_PCT:
        return True, f"上午观察窗口监测ETF最大绝对涨跌约{current_max_etf_abs:.2f}%"
    if current_spread >= A_SHARE_OPEN_ETF_SPREAD_PCT:
        return True, f"上午观察窗口监测ETF横截面分化约{current_spread:.2f}个百分点"
    return False, "实际开盘跳空及上午结构均未达到主动通知价值门槛"


def _a_share_event() -> dict | None:
    node = _a_share_node()
    if not node:
        return None
    current = read_json(CURRENT, {})
    market_date = str(current.get("market_date") or "")
    if not market_date or market_date != datetime.now(BEIJING).date().isoformat():
        return None
    snapshot_path = ROOT / str(current.get("latest_snapshot") or "")
    if not snapshot_path.exists():
        return None
    snapshot = read_json(snapshot_path, {})
    rows = [x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"]
    indices = {str(x.get("symbol")): x for x in rows if x.get("asset_class") == "A_SHARE_INDEX"}
    etfs = [x for x in rows if x.get("asset_class") == "ETF" and number(x.get("change_pct")) is not None]
    if not indices or not etfs:
        return None
    if node == "CLOSE" and str(current.get("latest_valid_node") or "") != "close":
        return None

    value_reason = "固定收盘总结"
    if node == "OPEN":
        valuable, value_reason = _a_share_open_is_valuable(indices, etfs)
        if not valuable:
            return None

    features = _feature_map()
    index_names = {"000001": "上证指数（000001）", "000688": "科创50指数（000688）", "399006": "创业板指（399006）"}
    headline: list[str] = [f"- **通知价值**：{value_reason}"]
    path_lines: list[str] = []
    for code in ("000001", "000688", "399006"):
        row = indices.get(code)
        if not row:
            continue
        if node == "OPEN":
            headline.append(f"- **{index_names[code]}**：实际开盘较前收{pct(_a_share_open_gap(row))}；当前{pct(number(row.get('change_pct')))}")
        else:
            headline.append(f"- **{index_names[code]}**：{pct(number(row.get('change_pct')))}")
        f = features.get(code) or {}
        if f:
            path_lines.append(f"- **{index_names[code]}**：首个连续竞价采样至当前{pct(number(f.get('path_change_pct')))}；自路径低点修复{pct(number(f.get('recovery_from_path_low_pct')))}，距路径高点{pct(number(f.get('retreat_from_path_high_pct')))}；最近10分钟斜率约{pct(number(f.get('recent_slope_pct_per_10m')))}。")
        else:
            path_lines.append(f"- **{index_names[code]}**：{ohlc_path_phrase(number(row.get('open')), number(row.get('high')), number(row.get('low')), number(row.get('close')))}")

    ranked = sorted(etfs, key=lambda x: float(x.get("change_pct") or 0), reverse=True)
    headline.append("- **ETF横截面领先**：" + "、".join(f"{x.get('provider_name') or x.get('symbol')}（{x.get('symbol')}）{pct(number(x.get('change_pct')))}" for x in ranked[:3]))
    headline.append("- **ETF横截面靠后**：" + "、".join(f"{x.get('provider_name') or x.get('symbol')}（{x.get('symbol')}）{pct(number(x.get('change_pct')))}" for x in ranked[-2:]))

    if node == "OPEN":
        title = "【异动提醒】A股开盘/上午结构出现有价值变化"
        implication = "实际开盘和上午结构已经形成值得占用注意力的指数跳空、ETF强弱或横截面分化；即使首个有效采样延迟，也保留原始开盘事实。"
        action = "打开ETF项目查看当前正式判断；没有风险许可、机会状态或持仓动作变化时，不因开盘信号机械交易。"
        boundary = "A股开盘不逢开必报；恢复窗口延长到上午收盘，约0.8%/1.5%/2个百分点仅控制注意力。"
    else:
        title = "【收盘总结】A股全天结构与ETF强弱"
        implication = "固定收盘总结确认全天指数、ETF横截面和日内路径；完整账户、风险许可、生命周期和CASE仍由ETF交易复盘负责。"
        action = "先看全天结构是否强化或破坏当前持仓/候选假设；完整交易结论以ETF交易复盘为准。"
        boundary = "只要同日verified close仍可追溯，固定收盘总结允许延迟补发；必须明确15:00价格有效时点，不能伪装成晚间新行情。"

    provider_as_of = max([str(x.get("as_of_beijing") or "") for x in rows if x.get("as_of_beijing")], default=str(snapshot.get("captured_at_beijing") or ""))
    if node == "CLOSE":
        market_as_of = f"{market_date} 15:00:00"
        time_lines = [f"- **价格有效时点**：{market_as_of}", f"- **provider补查/观测上限**：{provider_as_of or '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"]
    else:
        market_as_of = provider_as_of
        time_lines = [f"- **A股行情依据**：{market_as_of or '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"]

    content = render_summary(headline_lines=[f"- **节点**：{'开盘/上午有价值信号' if node == 'OPEN' else '15:00正式收盘'}"] + headline, path_lines=path_lines, implication=implication, action=action, as_of_lines=time_lines, boundary=boundary)
    return {
        "key": f"a-share-session-summary:{market_date}:{node}",
        "type": "A股市场总结" if node == "CLOSE" else "市场有价值事件",
        "event_type": "A_SHARE_SESSION_SUMMARY" if node == "CLOSE" else "A_SHARE_OPEN_VALUE_ALERT",
        "title": title,
        "content": content,
        "source": "CURRENT+intraday_path_features",
        "user_severity": "需要关注",
        "user_action": "查看结构摘要；正式交易动作以MASTER完整决策链为准",
        "confirmation_context": {"market_date": market_date, "session_node": node, "market_as_of_beijing": market_as_of, "provider_observed_as_of_beijing": provider_as_of, "value_reason": value_reason},
    }


def _apac_return(obj: dict) -> float | None:
    latest = obj.get("latest") or {}
    close = number(latest.get("close"))
    prev = number(latest.get("previous_close"))
    if prev is None:
        prev = number(obj.get("previous_close_reference"))
    return pct_change(close, prev)


def _apac_open_gap(obj: dict) -> float | None:
    latest = obj.get("latest") or {}
    prev = number(latest.get("previous_close"))
    if prev is None:
        prev = number(obj.get("previous_close_reference"))
    return pct_change(number(latest.get("open")), prev)


def _apac_selected(objects: dict, today: str) -> list[tuple[str, str, dict, dict]]:
    selected = []
    for code, label in APAC_SPECS:
        obj = objects.get(code) or {}
        latest = obj.get("latest") or {}
        if obj.get("quality_status") != "PASS":
            continue
        if str(latest.get("market_date_local") or "") != today or not latest.get("as_of_beijing"):
            continue
        selected.append((code, label, obj, latest))
    return selected


def _apac_tone(selected: list[tuple[str, str, dict, dict]]) -> str:
    returns = [x for x in (_apac_return(obj) for _, _, obj, _ in selected) if x is not None]
    if not returns:
        return "方向信息不足"
    avg = sum(returns) / len(returns)
    if all(x > 0 for x in returns):
        return "区域普遍偏强"
    if all(x < 0 for x in returns):
        return "区域普遍偏弱"
    if abs(avg) >= 1.0:
        return "区域分化但方向偏强" if avg > 0 else "区域分化但方向偏弱"
    return "区域分化/相对平稳"


def _a_share_reference(today: str) -> tuple[list[str], str]:
    current = read_json(CURRENT, {})
    if str(current.get("market_date") or "") != today:
        return [], ""
    snapshot_path = ROOT / str(current.get("latest_snapshot") or "")
    if not snapshot_path.exists():
        return [], ""
    snapshot = read_json(snapshot_path, {})
    names = {"000001": "上证指数（000001）", "000688": "科创50指数（000688）", "399006": "创业板指（399006）"}
    lines, times = [], []
    for row in snapshot.get("rows") or []:
        code = str(row.get("symbol") or "")
        if code not in names or row.get("quality_status") != "PASS":
            continue
        lines.append(f"- **A股同步反馈｜{names[code]}**：{pct(number(row.get('change_pct')))}")
        if row.get("as_of_beijing"):
            times.append(str(row.get("as_of_beijing")))
    return lines, max(times) if times else str(snapshot.get("captured_at_beijing") or "")


def _a_share_feedback(today: str) -> tuple[str, dict[str, dict], str]:
    current = read_json(CURRENT, {})
    if str(current.get("market_date") or "") != today:
        return "A股当日同步数据不可用", {}, ""
    snapshot_path = ROOT / str(current.get("latest_snapshot") or "")
    if not snapshot_path.exists():
        return "A股当日同步数据不可用", {}, ""
    snapshot = read_json(snapshot_path, {})
    rows = [x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"]
    row_map = {str(x.get("symbol") or ""): x for x in rows}
    index_names = {"000001": "上证指数（000001）", "000688": "科创50指数（000688）", "399006": "创业板指（399006）"}
    parts = []
    times = []
    for code in ("000001", "000688", "399006"):
        row = row_map.get(code)
        if not row:
            continue
        parts.append(f"{index_names[code]}{pct(number(row.get('change_pct')))}")
        if row.get("as_of_beijing"):
            times.append(str(row.get("as_of_beijing")))
    return ("、".join(parts) if parts else "A股核心指数同步数据不可用"), row_map, (max(times) if times else str(snapshot.get("captured_at_beijing") or ""))


def _apac_lead_path(open_gap: float, current_ret: float | None) -> str:
    if current_ret is None:
        return f"实际开盘较前收{pct(open_gap)}，当前涨跌不可用，暂不能判断跳空是否延续。"
    improvement = current_ret - open_gap
    if open_gap < 0:
        if current_ret >= 0:
            state = "负面跳空已基本收复并回到前收上方"
        elif improvement >= 0.6:
            state = "负面跳空明显收窄"
        elif improvement <= -0.4:
            state = "低开后继续走弱，开盘冲击扩大"
        else:
            state = "低开后仍维持偏弱，开盘冲击尚未解除"
        return f"低开{pct(open_gap)}后当前{pct(current_ret)}，{state}。"
    if current_ret <= 0:
        state = "正面跳空已基本回吐并跌回前收下方"
    elif improvement <= -0.6:
        state = "高开优势明显收窄"
    elif improvement >= 0.4:
        state = "高开后继续强化，开盘冲击扩大"
    else:
        state = "高开后仍保持偏强，开盘优势尚在"
    return f"高开{pct(open_gap)}后当前{pct(current_ret)}，{state}。"


def _apac_cross_market_line(selected: list[tuple[str, str, dict, dict]], lead_code: str) -> str:
    peers = []
    for code, label, obj, _ in selected:
        if code == lead_code:
            continue
        ret = _apac_return(obj)
        if ret is not None:
            peers.append((ret, label))
    if not peers:
        return "其他亚太主要市场当前缺少足够可比数据。"
    peers.sort(reverse=True)
    return "其他市场对照：" + "、".join(f"{label}{pct(ret)}" for ret, label in peers) + "。"


def _apac_quality_line(selected: list[tuple[str, str, dict, dict]], objects: dict, today: str) -> str:
    same_day = []
    for code, label in APAC_SPECS:
        obj = objects.get(code) or {}
        latest = obj.get("latest") or {}
        if str(latest.get("market_date_local") or "") == today and latest.get("as_of_beijing"):
            same_day.append((label, str(obj.get("quality_status") or "UNKNOWN"), str(obj.get("freshness_status") or "")))
    degraded = [label for label, quality, freshness in same_day if quality != "PASS" or freshness in {"DELAYED", "STALE", "MISSING"}]
    base = f"覆盖{len(same_day)}/4；触发判断使用PASS对象{len(selected)}/4"
    if degraded:
        return base + "；数据受限/延迟：" + "、".join(degraded) + "。"
    return base + "；当前未发现需单列的数据质量降级。"


def _apac_open_implication(lead_code: str, lead_label: str, lead_open_gap: float, lead_current: float | None, tone: str, today: str) -> tuple[str, str]:
    a_share_text, row_map, a_time = _a_share_feedback(today)
    if lead_open_gap < 0 and lead_current is not None and lead_current >= -0.2:
        external = f"外部结构：{lead_label}低开冲击已经明显修复，当前不能把开盘负面跳空继续解释为新的区域风险强化。"
    elif lead_open_gap > 0 and lead_current is not None and lead_current <= 0.2:
        external = f"外部结构：{lead_label}高开优势已经明显回吐，当前不能把开盘正面跳空继续解释为新的区域风险强化。"
    else:
        external = f"外部结构：{lead_label}的实际开盘异常仍需结合当前{pct(lead_current)}和区域判断“{tone}”继续验证是否延续。"

    local = f"本地传导：A股同步反馈为{a_share_text}；外部信号只有被A股自身价格结构接受，才可能改变当前ETF判断。"
    direct = APAC_DIRECT_ETF.get(lead_code)
    if direct:
        etf_code, etf_label = direct
        row = row_map.get(etf_code)
        etf_ret = number(row.get("change_pct")) if row else None
        etf_text = pct(etf_ret) if etf_ret is not None else "当前数据不可用"
        etf = f"ETF自身反馈：直接相关的{etf_label}当前{etf_text}；该外部事件只作为其观察证据，不能绕过ETF自身结构、相对强弱、风险收益和资本效率。"
    else:
        etf = "ETF自身反馈：当前没有需要机械绑定的直接监测ETF，区域指数变化只进入跨市场证据层。"
    opportunity = "机会判断：本通知不生成Trial/Confirm或卖出动作；只有外部结构、本地传导和ETF自身反馈共同改变正式判断时，才进入交易链。"
    return external + "\n\n" + local + "\n\n" + etf + "\n\n" + opportunity, a_time


def _render_apac_open(*, headline_lines: list[str], path_lines: list[str], implication: str, action: str, as_of_lines: list[str], boundary: str) -> str:
    return (
        "### 核心结论\n"
        + "\n".join(headline_lines)
        + "\n\n### 关键路径\n"
        + "\n".join(path_lines)
        + "\n\n### 对ETF系统的影响\n"
        + implication
        + "\n\n### 当前动作\n**"
        + action
        + "**\n\n### 数据与边界\n"
        + "\n".join(as_of_lines)
        + "\n"
        + f"- **边界**：{boundary}"
    )


def _latest_primary_apac(today: str) -> dict | None:
    state = read_json(NOTIFICATION_STATE, {})
    for item in reversed(state.get("notifications") or []):
        if str(item.get("event_type") or "") != "APAC_SESSION_SUMMARY":
            continue
        ctx = item.get("confirmation_context") or {}
        if str(ctx.get("market_date") or "") == today and str(ctx.get("session_node") or "") == "PRIMARY_CLOSE":
            return item
    return None


def _apac_lines(selected: list[tuple[str, str, dict, dict]], mark_hk_live: bool) -> tuple[list[str], list[str], list[str], float | None]:
    headline, path_lines, times = [], [], []
    hstech_ret = None
    for code, label, obj, latest in selected:
        ret = _apac_return(obj)
        if code == "HSTECH":
            hstech_ret = ret
        status = "（仍在交易）" if mark_hk_live and code == "HSTECH" else ""
        headline.append(f"- **{label}{status}**：较前收{pct(ret)}")
        path_lines.append(f"- **{label}{status}**：{ohlc_path_phrase(number(latest.get('open')), number(latest.get('high')), number(latest.get('low')), number(latest.get('close')))}")
        if latest.get("as_of_beijing"):
            times.append(str(latest.get("as_of_beijing")))
    return headline, path_lines, times, hstech_ret


def _apac_event() -> dict | None:
    dt = datetime.now(BEIJING)
    minute = dt.hour * 60 + dt.minute
    today = dt.date().isoformat()
    context = read_json(OVERSEAS, {})
    objects = context.get("objects") or {}
    selected = _apac_selected(objects, today)
    if not selected:
        return None

    # Actual APAC opening-gap facts remain recoverable through the morning.
    # Intraday cumulative moves after a normal open belong to the unified
    # five-category market-event engine; they must not be relabeled as opening
    # anomalies merely because the current return later crosses this threshold.
    if 8 * 60 <= minute <= 11 * 60 + 30:
        candidates: list[tuple[float, str, str, float]] = []
        for code, label, obj, _ in selected:
            open_gap = _apac_open_gap(obj)
            if open_gap is not None and abs(open_gap) >= APAC_OPEN_SIGNAL_ABS_PCT:
                candidates.append((abs(open_gap), code, label, open_gap))
        if not candidates:
            return None
        _, lead_code, lead_label, lead_open_gap = max(candidates, key=lambda x: x[0])
        direction = "UP" if lead_open_gap > 0 else "DOWN"
        tone = _apac_tone(selected)
        lead_obj = objects.get(lead_code) or {}
        lead_latest = lead_obj.get("latest") or {}
        lead_current = _apac_return(lead_obj)
        times = [str(latest.get("as_of_beijing")) for _, _, _, latest in selected if latest.get("as_of_beijing")]
        path_lines = [
            f"- **触发对象**：{_apac_lead_path(lead_open_gap, lead_current)}",
            f"- **区域对照**：{_apac_cross_market_line(selected, lead_code)}",
        ]
        implication, a_time = _apac_open_implication(lead_code, lead_label, lead_open_gap, lead_current, tone, today)
        headline = [
            "- **节点**：亚太错位开盘异常",
            f"- **触发对象**：{lead_label}",
            f"- **实际开盘较前收**：{pct(lead_open_gap)}",
            f"- **当前较前收**：{pct(lead_current)}",
            f"- **区域结构**：{tone}",
            f"- **数据质量**：{_apac_quality_line(selected, objects, today)}",
        ]
        title = f"【异动提醒】{lead_label}实际开盘出现异常跳空"
        content = _render_apac_open(
            headline_lines=headline,
            path_lines=path_lines,
            implication=implication,
            action="把该事件纳入最近A股正式节点复核；不因单一海外/区域指数跳空机械买卖，重点看A股是否接受以及直接相关ETF自身反馈。",
            as_of_lines=[
                f"- **触发对象行情时点（北京时间）**：{lead_latest.get('as_of_beijing') or '未提供'}",
                f"- **亚太对象最新有效时点上限**：{max(times) if times else '未提供'}",
                f"- **A股同步参考时点**：{a_time or '未提供'}",
                f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}",
            ],
            boundary="约0.8%的门槛只检查实际开盘价相对前收；开盘后的累计涨跌不回填成开盘异常。覆盖数量与数据质量分开表达，DEGRADED/延迟对象不得被包装成全部实时FRESH。",
        )
        return {"key": f"apac-open-signal:{today}:{lead_code}:{direction}", "type": "市场有价值事件", "event_type": "APAC_OPEN_SIGNAL", "title": title, "content": content, "source": "overseas_context+CURRENT", "security_code": lead_code, "security_name": lead_label.split("（")[0], "user_severity": "需要关注", "user_action": "纳入最近A股节点，检查本地传导和直接相关ETF自身反馈，不机械交易", "confirmation_context": {"market_date": today, "session_node": "OPEN_SIGNAL", "lead_code": lead_code, "lead_open_gap_pct": lead_open_gap, "lead_change_pct": lead_current, "tone": tone, "market_as_of_beijing": lead_latest.get("as_of_beijing") or (max(times) if times else ""), "a_share_as_of_beijing": a_time}}

    # Primary APAC close remains most valuable while A-share is still trading,
    # but may be recovered until Hong Kong close if GitHub/provider was delayed.
    if 14 * 60 + 35 <= minute < 16 * 60 + 5:
        tone = _apac_tone(selected)
        headline, path_lines, times, hstech_ret = _apac_lines(selected, mark_hk_live=True)
        a_lines, a_time = _a_share_reference(today)
        headline = [f"- **区域判断**：{tone}", f"- **当日有效覆盖**：{len(selected)}/4", "- **时点语义**：日本/韩国/台湾已进入收盘结果；香港仍可能在交易"] + headline + a_lines
        title = f"【收盘总结】亚太主要市场收盘，A股参考：{tone}"
        content = render_summary(
            headline_lines=["- **节点**：日韩台主要市场收盘后"] + headline,
            path_lines=path_lines,
            implication="优先服务A股尾盘；若因调度或provider延迟而补发，仍保留日韩台收盘事实，但必须以真实时点解释，不能伪装成当前刚发生。",
            action="将亚太收盘结构纳入A股尾盘/下一节点复核，重点检查相关指数、持仓ETF和观察ETF自身反馈。",
            as_of_lines=[f"- **亚太各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **A股同步参考时点**：{a_time or '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
            boundary="香港尚未收盘时必须标为盘中；不同市场真实时点不伪装同步。",
        )
        return {"key": f"apac-session-summary:{today}:PRIMARY_CLOSE", "type": "亚太市场总结", "event_type": "APAC_SESSION_SUMMARY", "title": title, "content": content, "source": "overseas_context+CURRENT", "user_severity": "需要关注", "user_action": "纳入A股尾盘/最近节点跨市场复核，不机械交易", "confirmation_context": {"market_date": today, "session_node": "PRIMARY_CLOSE", "tone": tone, "coverage": len(selected), "hstech_change_pct": hstech_ret, "market_as_of_beijing": max(times) if times else "", "a_share_as_of_beijing": a_time}}

    if minute >= 16 * 60 + 5:
        prior = _latest_primary_apac(today)
        tone = _apac_tone(selected)
        headline, path_lines, times, hstech_ret = _apac_lines(selected, mark_hk_live=False)
        previous_tone, previous_hstech = "", None
        if prior:
            prior_ctx = prior.get("confirmation_context") or {}
            previous_tone = str(prior_ctx.get("tone") or "")
            previous_hstech = number(prior_ctx.get("hstech_change_pct"))
        hstech_delta = hstech_ret - previous_hstech if hstech_ret is not None and previous_hstech is not None else None
        material = prior is None or (hstech_delta is not None and abs(hstech_delta) >= APAC_LATE_HK_CHANGE_PCT) or (previous_tone and tone != previous_tone)
        if not material:
            return None
        fallback = prior is None
        headline = [f"- **区域最终判断**：{tone}", f"- **此前判断变化**：{'主总结缺失，本条作为兜底' if fallback else (f'恒生科技变化约{hstech_delta:+.2f}个百分点' if hstech_delta is not None else '区域方向发生变化')}"] + headline
        title = f"【收盘总结】亚太香港收盘后{'主总结兜底' if fallback else '区域判断发生实质变化'}"
        content = render_summary(headline_lines=["- **节点**：香港16:00收盘后的价值更新"] + headline, path_lines=path_lines, implication="香港收盘只在实质改变此前亚太判断时追加；属于A股收盘后的新证据，不能反向改写15:00已形成事实。", action="把更新后的亚太结构纳入下一A股交易节点。", as_of_lines=[f"- **各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"], boundary="本节点价值驱动，不是形式化第二次收盘总结；约0.8个百分点的香港增量变化只控制注意力。")
        return {"key": f"apac-session-summary:{today}:HK_LATE_UPDATE", "type": "亚太市场总结", "event_type": "APAC_SESSION_SUMMARY", "title": title, "content": content, "source": "overseas_context", "user_severity": "需要关注", "user_action": "纳入下一A股交易节点，不反向改写当日A股事实", "confirmation_context": {"market_date": today, "session_node": "HK_LATE_UPDATE", "tone": tone, "previous_tone": previous_tone, "hstech_change_pct": hstech_ret, "hstech_change_since_primary_pct": hstech_delta, "market_as_of_beijing": max(times) if times else ""}}
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a-share", "apac"], required=True)
    args = parser.parse_args()
    event = _a_share_event() if args.market == "a-share" else _apac_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(event, policy="开盘仅推有价值结构，但实际session事实允许在同日有效恢复；A股固定收盘允许延迟补发；亚太开盘通知优先表达触发对象关键路径、本地传导、直接相关ETF反馈及数据质量；亚太主要收盘优先服务A股尾盘、错过窄窗口后仍可恢复。阈值适度放宽，Point-in-Time与去重保持。")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())