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

# Notification-attention thresholds only. They do not create trading rules.
A_SHARE_OPEN_INDEX_ABS_PCT = 1.0
A_SHARE_OPEN_ETF_ABS_PCT = 2.0
A_SHARE_OPEN_ETF_SPREAD_PCT = 2.5
APAC_OPEN_SIGNAL_ABS_PCT = 1.0
APAC_LATE_HK_CHANGE_PCT = 1.0

APAC_SPECS = [
    ("N225", "日经225指数（N225）"),
    ("KOSPI", "韩国综合指数（KOSPI）"),
    ("TWII", "台湾加权指数（TWII）"),
    ("HSTECH", "恒生科技指数（HSTECH）"),
]


def _minute_now() -> int:
    dt = datetime.now(BEIJING)
    return dt.hour * 60 + dt.minute


def _feature_map() -> dict[str, dict]:
    data = read_json(PATH_FEATURES, {})
    return {str(x.get("symbol") or ""): x for x in (data.get("features") or [])}


def _a_share_node() -> str:
    minute = _minute_now()
    # Preserve actual opening facts through the first hour so one delayed/missed
    # GitHub schedule cannot erase a material opening gap after price fades.
    if 9 * 60 + 35 <= minute <= 10 * 60 + 30:
        return "OPEN"
    if 15 * 60 <= minute <= 15 * 60 + 30:
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
        return True, f"开盘观察窗口核心指数最大绝对涨跌约{current_max_index:.2f}%"
    if current_max_etf_abs >= A_SHARE_OPEN_ETF_ABS_PCT:
        return True, f"开盘观察窗口监测ETF最大绝对涨跌约{current_max_etf_abs:.2f}%"
    if current_spread >= A_SHARE_OPEN_ETF_SPREAD_PCT:
        return True, f"开盘观察窗口监测ETF横截面分化约{current_spread:.2f}个百分点"
    return False, "实际开盘跳空及首小时结构均未达到主动通知价值门槛"


def _a_share_event() -> dict | None:
    node = _a_share_node()
    if not node:
        return None
    current = read_json(CURRENT, {})
    market_date = str(current.get("market_date") or "")
    if not market_date:
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
            path_lines.append(
                f"- **{index_names[code]}**：首个连续竞价采样至当前{pct(number(f.get('path_change_pct')))}；"
                f"自路径低点修复{pct(number(f.get('recovery_from_path_low_pct')))}，距路径高点{pct(number(f.get('retreat_from_path_high_pct')))}；"
                f"最近10分钟斜率约{pct(number(f.get('recent_slope_pct_per_10m')))}。"
            )
        else:
            path_lines.append(f"- **{index_names[code]}**：{ohlc_path_phrase(number(row.get('open')), number(row.get('high')), number(row.get('low')), number(row.get('close')))}")

    ranked = sorted(etfs, key=lambda x: float(x.get("change_pct") or 0), reverse=True)
    top = ranked[:3]
    bottom = ranked[-2:]
    headline.append("- **ETF横截面领先**：" + "、".join(f"{x.get('provider_name') or x.get('symbol')}（{x.get('symbol')}）{pct(number(x.get('change_pct')))}" for x in top))
    headline.append("- **ETF横截面靠后**：" + "、".join(f"{x.get('provider_name') or x.get('symbol')}（{x.get('symbol')}）{pct(number(x.get('change_pct')))}" for x in bottom))

    if node == "OPEN":
        title = "【异动提醒】A股开盘结构出现有价值变化"
        implication = "开盘已出现值得占用注意力的指数跳空、ETF强弱或横截面分化；实际开盘事实不会因为首个有效采样延迟、价格随后回落而被抹掉，但仍需承接、相对强弱、风险收益和完整交易链验证。"
        action = "打开ETF项目查看当前正式判断；若没有风险许可、机会状态或持仓动作变化，不因开盘信号机械交易。"
        boundary = "A股开盘不逢开必报；实际开盘跳空在首小时恢复窗口内持续作为事实。门槛仅控制注意力，不属于MASTER交易规则。"
    else:
        title = "【收盘总结】A股全天结构与ETF强弱"
        implication = "固定收盘总结用于确认全天指数、ETF横截面和日内路径最终结果；15:30的ETF交易复盘仍负责账户、风险许可、生命周期、资本效率、卖出判断和CASE闭环。"
        action = "先看全天结构是否强化或破坏当前持仓/候选假设；完整交易结论以随后ETF交易复盘为准。"
        boundary = "收盘PushPlus不重复完整正式复盘，也不根据单日涨跌机械生成买卖动作。"

    provider_as_of = max([str(x.get("as_of_beijing") or "") for x in rows if x.get("as_of_beijing")], default=str(snapshot.get("captured_at_beijing") or ""))
    if node == "CLOSE":
        market_as_of = f"{market_date} 15:00:00"
        time_lines = [f"- **价格有效时点**：{market_as_of}", f"- **provider补查/观测上限**：{provider_as_of or '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"]
    else:
        market_as_of = provider_as_of
        time_lines = [f"- **A股行情依据**：{market_as_of or '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"]

    content = render_summary(
        headline_lines=[f"- **节点**：{'开盘有价值信号' if node == 'OPEN' else '15:00正式收盘'}"] + headline,
        path_lines=path_lines,
        implication=implication,
        action=action,
        as_of_lines=time_lines,
        boundary=boundary,
    )
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
        if str(latest.get("market_date_local") or "") != today:
            continue
        if not latest.get("as_of_beijing"):
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
    lines = []
    times = []
    for row in snapshot.get("rows") or []:
        code = str(row.get("symbol") or "")
        if code not in names or row.get("quality_status") != "PASS":
            continue
        lines.append(f"- **A股同步反馈｜{names[code]}**：{pct(number(row.get('change_pct')))}")
        if row.get("as_of_beijing"):
            times.append(str(row.get("as_of_beijing")))
    return lines, max(times) if times else str(snapshot.get("captured_at_beijing") or "")


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
    headline: list[str] = []
    path_lines: list[str] = []
    times: list[str] = []
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

    # APAC markets open at different times. Use the actual opening gap as a
    # persistent same-session fact, then current return as secondary evidence.
    # This prevents a late first successful pulse from erasing a Korean/Japanese/
    # Taiwan/Hong Kong opening anomaly after an early fade.
    if 8 * 60 <= minute <= 10 * 60 + 30:
        candidates: list[tuple[float, str, str, float, float | None]] = []
        for code, label, obj, _ in selected:
            open_gap = _apac_open_gap(obj)
            current_ret = _apac_return(obj)
            trigger = open_gap if open_gap is not None and abs(open_gap) >= APAC_OPEN_SIGNAL_ABS_PCT else current_ret
            if trigger is not None and abs(trigger) >= APAC_OPEN_SIGNAL_ABS_PCT:
                candidates.append((abs(trigger), code, label, trigger, open_gap))
        if not candidates:
            return None
        _, lead_code, lead_label, lead_ret, lead_open_gap = max(candidates, key=lambda x: x[0])
        direction = "UP" if lead_ret > 0 else "DOWN"
        tone = _apac_tone(selected)
        headline, path_lines, times, _ = _apac_lines(selected, mark_hk_live=True)
        lead_obj = objects.get(lead_code) or {}
        lead_current = _apac_return(lead_obj)
        headline = [
            f"- **触发对象**：{lead_label}",
            f"- **实际开盘较前收**：{pct(lead_open_gap)}",
            f"- **当前较前收**：{pct(lead_current)}",
            f"- **当前区域判断**：{tone}",
            f"- **当日已取得有效行情市场数**：{len(selected)}/4",
        ] + headline
        title = f"【异动提醒】{lead_label}开盘出现有价值变化"
        content = render_summary(
            headline_lines=["- **节点**：亚太市场错位开盘中的实时价值信号"] + headline,
            path_lines=path_lines,
            implication="亚太各市场并不同步开盘；某一先开市场的实际开盘跳空本身可以成为A股盘前/早盘的外部结构证据，且不会因为首个成功pulse延迟、价格随后回落而被抹掉。重点仍是随后其他市场及A股是否共振、减弱或形成背离。",
            action="把该信号纳入最近A股决策节点；如果其他亚太市场随后给出相反反馈，应更新区域结构判断，而不是机械沿用第一条开盘方向。",
            as_of_lines=[f"- **各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
            boundary="亚太开盘不逢开必报；实际开盘跳空在早盘恢复窗口内持续作为事实，约1%的门槛只控制注意力，不属于MASTER交易规则。\n\n> 外部结构 → 本地传导 → ETF自身反馈 → 机会判断。",
        )
        return {
            "key": f"apac-open-signal:{today}:{lead_code}:{direction}",
            "type": "市场有价值事件",
            "event_type": "APAC_OPEN_SIGNAL",
            "title": title,
            "content": content,
            "source": "overseas_context",
            "security_code": lead_code,
            "security_name": lead_label.split("（")[0],
            "user_severity": "需要关注",
            "user_action": "纳入最近A股节点验证区域共振或背离，不机械交易",
            "confirmation_context": {"market_date": today, "session_node": "OPEN_SIGNAL", "lead_code": lead_code, "lead_open_gap_pct": lead_open_gap, "lead_change_pct": lead_current, "tone": tone, "market_as_of_beijing": max(times) if times else ""},
        }

    if 14 * 60 + 35 <= minute <= 14 * 60 + 55:
        tone = _apac_tone(selected)
        headline, path_lines, times, hstech_ret = _apac_lines(selected, mark_hk_live=True)
        a_lines, a_time = _a_share_reference(today)
        headline = [f"- **区域判断**：{tone}", f"- **当日有效覆盖**：{len(selected)}/4", "- **时点语义**：日本/韩国/台湾进入收盘结果区间；香港仍在交易"] + headline + a_lines
        title = f"【收盘总结】亚太主要市场收盘，A股尾盘参考：{tone}"
        content = render_summary(
            headline_lines=["- **节点**：日韩台主要市场收盘后、A股15:00收盘前"] + headline,
            path_lines=path_lines,
            implication="这个节点的价值在于A股仍有最后约20分钟交易时间：日韩台已经给出接近完整的当日结果，香港继续提供同步中国科技风险偏好，可用于判断A股尾盘是区域共振、独立强弱还是出现传导背离。",
            action="把亚太收盘结构立即纳入A股尾盘复核，重点检查相关指数、持仓ETF和观察ETF自身反馈；只有正式交易链结论变化时才执行动作。",
            as_of_lines=[f"- **亚太各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **A股同步参考时点**：{a_time or '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
            boundary="香港此时尚未收盘，必须明确标为盘中；本通知不等待香港16:00才首次总结，也不把不同市场的真实时点伪装成同步收盘。\n\n> 外部结构 → 本地传导 → ETF自身反馈 → 机会判断。",
        )
        return {
            "key": f"apac-session-summary:{today}:PRIMARY_CLOSE",
            "type": "亚太市场总结",
            "event_type": "APAC_SESSION_SUMMARY",
            "title": title,
            "content": content,
            "source": "overseas_context+CURRENT",
            "user_severity": "需要关注",
            "user_action": "立即纳入A股尾盘跨市场复核，不机械交易",
            "confirmation_context": {"market_date": today, "session_node": "PRIMARY_CLOSE", "tone": tone, "coverage": len(selected), "hstech_change_pct": hstech_ret, "market_as_of_beijing": max(times) if times else "", "a_share_as_of_beijing": a_time},
        }

    if 16 * 60 + 5 <= minute <= 16 * 60 + 30:
        prior = _latest_primary_apac(today)
        tone = _apac_tone(selected)
        headline, path_lines, times, hstech_ret = _apac_lines(selected, mark_hk_live=False)
        previous_tone = ""
        previous_hstech = None
        if prior:
            prior_ctx = prior.get("confirmation_context") or {}
            previous_tone = str(prior_ctx.get("tone") or "")
            previous_hstech = number(prior_ctx.get("hstech_change_pct"))
        hstech_delta = None
        if hstech_ret is not None and previous_hstech is not None:
            hstech_delta = hstech_ret - previous_hstech
        material = prior is None or (hstech_delta is not None and abs(hstech_delta) >= APAC_LATE_HK_CHANGE_PCT) or (previous_tone and tone != previous_tone)
        if not material:
            return None

        fallback = prior is None
        headline = [f"- **区域最终判断**：{tone}", f"- **14:35后判断变化**：{'主总结缺失，本条作为兜底' if fallback else (f'恒生科技变化约{hstech_delta:+.2f}个百分点' if hstech_delta is not None else '区域方向发生变化')}"] + headline
        title = f"【收盘总结】亚太香港收盘后{'主总结兜底' if fallback else '区域判断发生实质变化'}"
        content = render_summary(
            headline_lines=["- **节点**：香港16:00收盘后的价值更新"] + headline,
            path_lines=path_lines,
            implication="香港收盘只在它实质改变14:35亚太判断时追加通知；它属于A股收盘后的新证据，用于下一交易日盘前，不能反向改写A股15:00已经形成的正式事实。",
            action="把更新后的亚太结构纳入下一A股交易节点；如果没有实质变化，系统保持静默，不为形式完整再发一篇重复总结。",
            as_of_lines=[f"- **各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
            boundary="本节点是价值驱动的晚间补充，不是固定第二次亚太收盘总结。\n\n> 外部结构 → 本地传导 → ETF自身反馈 → 机会判断。",
        )
        return {
            "key": f"apac-session-summary:{today}:HK_LATE_UPDATE",
            "type": "亚太市场总结",
            "event_type": "APAC_SESSION_SUMMARY",
            "title": title,
            "content": content,
            "source": "overseas_context",
            "user_severity": "需要关注",
            "user_action": "纳入下一A股交易节点，不反向改写当日A股事实",
            "confirmation_context": {"market_date": today, "session_node": "HK_LATE_UPDATE", "tone": tone, "previous_tone": previous_tone, "hstech_change_pct": hstech_ret, "hstech_change_since_primary_pct": hstech_delta, "market_as_of_beijing": max(times) if times else ""},
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a-share", "apac"], required=True)
    args = parser.parse_args()
    event = _a_share_event() if args.market == "a-share" else _apac_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(
        event,
        policy="开盘只推送有价值信号，但实际开盘事实在首小时恢复窗口内持续保留，避免首个GitHub pulse延迟后被当前价格抹掉；A股和美股固定收盘，亚太主要收盘总结前移服务A股尾盘，香港收盘仅在实质改变判断时追加。",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
