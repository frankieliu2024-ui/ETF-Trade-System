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

# Notification-attention thresholds only. They do not create trading rules.
A_SHARE_OPEN_INDEX_ABS_PCT = 1.0
A_SHARE_OPEN_ETF_ABS_PCT = 2.0
A_SHARE_OPEN_ETF_SPREAD_PCT = 2.5
APAC_OPEN_SIGNAL_ABS_PCT = 1.0

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
    if 9 * 60 + 35 <= minute <= 10 * 60:
        return "OPEN"
    if 15 * 60 <= minute <= 15 * 60 + 30:
        return "CLOSE"
    return ""


def _a_share_open_is_valuable(indices: dict[str, dict], etfs: list[dict]) -> tuple[bool, str]:
    index_changes = [abs(float(x.get("change_pct"))) for x in indices.values() if number(x.get("change_pct")) is not None]
    etf_changes = [float(x.get("change_pct")) for x in etfs if number(x.get("change_pct")) is not None]
    max_index = max(index_changes, default=0.0)
    max_etf_abs = max((abs(x) for x in etf_changes), default=0.0)
    spread = (max(etf_changes) - min(etf_changes)) if etf_changes else 0.0
    if max_index >= A_SHARE_OPEN_INDEX_ABS_PCT:
        return True, f"核心指数开盘绝对涨跌达到约{max_index:.2f}%"
    if max_etf_abs >= A_SHARE_OPEN_ETF_ABS_PCT:
        return True, f"监测ETF开盘最大绝对涨跌达到约{max_etf_abs:.2f}%"
    if spread >= A_SHARE_OPEN_ETF_SPREAD_PCT:
        return True, f"监测ETF开盘横截面分化约{spread:.2f}个百分点"
    return False, "开盘结构未达到主动通知价值门槛"


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
        title = "【A股市场｜开盘有价值信号】结构出现明显变化"
        implication = "开盘已出现值得占用注意力的指数幅度、ETF强弱或横截面分化；这只是早盘结构证据，仍需承接、相对强弱、风险收益和完整交易链验证。"
        action = "打开ETF项目查看当前正式判断；若没有风险许可、机会状态或持仓动作变化，不因开盘信号机械交易。"
        boundary = "A股开盘不再固定推送；只有达到通知价值门槛才发。门槛仅控制注意力，不属于MASTER交易规则。"
    else:
        title = "【A股市场｜收盘总结】全天结构与ETF强弱"
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
        "type": "A股市场总结",
        "event_type": "A_SHARE_SESSION_SUMMARY",
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


def _apac_event() -> dict | None:
    dt = datetime.now(BEIJING)
    minute = dt.hour * 60 + dt.minute
    today = dt.date().isoformat()
    context = read_json(OVERSEAS, {})
    objects = context.get("objects") or {}
    selected = _apac_selected(objects, today)
    if not selected:
        return None

    # Asia-Pacific markets open at different times. Do not wait for all of them:
    # between 08:00 and 10:30, any same-day market with a material opening move
    # can create one value-driven APAC opening signal.
    if 8 * 60 <= minute <= 10 * 60 + 30:
        candidates: list[tuple[float, str, str, dict, dict, float]] = []
        for code, label, obj, latest in selected:
            ret = _apac_return(obj)
            if ret is not None and abs(ret) >= APAC_OPEN_SIGNAL_ABS_PCT:
                candidates.append((abs(ret), code, label, obj, latest, ret))
        if not candidates:
            return None
        _, lead_code, lead_label, _, _, lead_ret = max(candidates, key=lambda x: x[0])
        direction = "UP" if lead_ret > 0 else "DOWN"
        tone = _apac_tone(selected)
        headline = [f"- **触发对象**：{lead_label}，较前收{pct(lead_ret)}", f"- **当前区域判断**：{tone}", f"- **已开盘且数据有效市场数**：{len(selected)}/4"]
        path_lines = []
        times = []
        for code, label, obj, latest in selected:
            ret = _apac_return(obj)
            headline.append(f"- **{label}**：较前收{pct(ret)}")
            path_lines.append(f"- **{label}**：{ohlc_path_phrase(number(latest.get('open')), number(latest.get('high')), number(latest.get('low')), number(latest.get('close')))}")
            times.append(str(latest.get("as_of_beijing") or ""))
        title = f"【亚太市场｜开盘有价值信号】{lead_label}{pct(lead_ret)}"
        content = render_summary(
            headline_lines=["- **节点**：亚太市场错位开盘中的实时价值信号"] + headline,
            path_lines=path_lines,
            implication="亚太各市场并不同步开盘；某一先开市场出现明显方向时，本身就可以成为A股盘前/早盘的外部结构证据，无需等待台湾或香港全部开盘。重点是随后其他市场及A股是否共振、减弱或形成背离。",
            action="把该信号纳入最近A股决策节点；如果其他亚太市场随后给出相反反馈，应更新区域结构判断，而不是机械沿用第一条开盘方向。",
            as_of_lines=[f"- **各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
            boundary="亚太开盘不固定推送；只在已有开盘市场出现约1%以上显著方向时发送。该门槛只控制注意力，不属于MASTER交易规则。\n\n> 外部结构 → 本地传导 → ETF自身反馈 → 机会判断。",
        )
        return {
            "key": f"apac-open-signal:{today}:{lead_code}:{direction}",
            "type": "亚太市场开盘信号",
            "event_type": "APAC_OPEN_SIGNAL",
            "title": title,
            "content": content,
            "source": "overseas_context",
            "security_code": lead_code,
            "security_name": lead_label.split("（")[0],
            "user_severity": "需要关注",
            "user_action": "纳入最近A股节点验证区域共振或背离，不机械交易",
            "confirmation_context": {"market_date": today, "session_node": "OPEN_SIGNAL", "lead_code": lead_code, "lead_change_pct": lead_ret, "tone": tone, "market_as_of_beijing": max(times) if times else ""},
        }

    # One fixed APAC close summary after Hong Kong, the latest of the four core
    # markets, has closed. Markets that are closed for a local holiday are not
    # fabricated; the summary states actual coverage.
    if 16 * 60 + 5 <= minute <= 16 * 60 + 30:
        tone = _apac_tone(selected)
        headline = [f"- **综合判断**：{tone}", f"- **当日有效覆盖**：{len(selected)}/4"]
        path_lines = []
        times = []
        for code, label, obj, latest in selected:
            ret = _apac_return(obj)
            headline.append(f"- **{label}**：较前收{pct(ret)}")
            path_lines.append(f"- **{label}**：{ohlc_path_phrase(number(latest.get('open')), number(latest.get('high')), number(latest.get('low')), number(latest.get('close')))}")
            times.append(str(latest.get("as_of_beijing") or ""))
        title = f"【亚太市场｜收盘总结】日韩台港全天{tone}"
        content = render_summary(
            headline_lines=["- **节点**：亚太主要市场收盘总结"] + headline,
            path_lines=path_lines,
            implication="日韩台港全天结果用于判断亚洲风险偏好是否形成区域共振、分化，或与A股出现独立反馈。香港16:00收盘晚于A股，因此其中香港部分属于A股收盘后的补充证据，进入下一A股交易日盘前。",
            action="把亚太收盘结构与美股收盘、A股自身收盘反馈一起进入下一交易节点；出现背离时优先寻找本地独立驱动，不机械向海外收敛。",
            as_of_lines=[f"- **各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
            boundary="亚太收盘固定总结按实际可用市场形成，不伪造当地休市市场的当日行情；各市场provider时点分别保留。总结只提供结构证据，不直接生成A股交易动作。\n\n> 外部结构 → 本地传导 → ETF自身反馈 → 机会判断。",
        )
        return {
            "key": f"apac-session-summary:{today}:CLOSE",
            "type": "亚太市场总结",
            "event_type": "APAC_SESSION_SUMMARY",
            "title": title,
            "content": content,
            "source": "overseas_context",
            "user_severity": "需要关注",
            "user_action": "纳入下一A股交易节点的跨市场验证，不机械交易",
            "confirmation_context": {"market_date": today, "session_node": "CLOSE", "tone": tone, "coverage": len(selected), "market_as_of_beijing": max(times) if times else ""},
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
        policy="市场固定为A股/美股/亚太三类；开盘只推送有价值信号，收盘固定总结；各市场错位开盘按真实时点解释；总结只提供结构证据，不替代正式交易决策。",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
