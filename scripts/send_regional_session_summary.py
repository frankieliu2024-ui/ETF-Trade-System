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


def _bj_node(market: str) -> str:
    dt = datetime.now(BEIJING)
    minute = dt.hour * 60 + dt.minute
    if market == "a-share":
        if 9 * 60 + 35 <= minute <= 10 * 60:
            return "OPEN"
        if 15 * 60 <= minute <= 15 * 60 + 30:
            return "CLOSE"
    if market == "asia":
        if 9 * 60 + 5 <= minute <= 9 * 60 + 30:
            return "OPEN"
        if 14 * 60 + 30 <= minute <= 14 * 60 + 55:
            return "CLOSE"
    if market == "hk":
        if 9 * 60 + 35 <= minute <= 10 * 60:
            return "OPEN"
        if 16 * 60 + 5 <= minute <= 16 * 60 + 30:
            return "CLOSE"
    return ""


def _feature_map() -> dict[str, dict]:
    data = read_json(PATH_FEATURES, {})
    return {str(x.get("symbol") or ""): x for x in (data.get("features") or [])}


def _a_share_event() -> dict | None:
    node = _bj_node("a-share")
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
    features = _feature_map()

    index_names = {"000001": "上证指数（000001）", "000688": "科创50指数（000688）", "399006": "创业板指（399006）"}
    headline: list[str] = []
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
    top_text = "、".join(f"{x.get('provider_name') or x.get('symbol')}（{x.get('symbol')}）{pct(number(x.get('change_pct')))}" for x in top)
    bottom_text = "、".join(f"{x.get('provider_name') or x.get('symbol')}（{x.get('symbol')}）{pct(number(x.get('change_pct')))}" for x in bottom)
    headline.extend([f"- **ETF横截面领先**：{top_text}", f"- **ETF横截面靠后**：{bottom_text}"])

    if node == "OPEN":
        title = "【A股开盘｜即时摘要】开盘结构与ETF强弱"
        implication = "开盘后的第一轮连续竞价已经形成，可用于识别今天早盘风险偏好、科技/防御分化和ETF横截面领先方向；但开盘强弱仍需后续承接、相对强弱变化和完整风险收益判断验证。"
        action = "把领先ETF和指数结构纳入当前机会扫描；若另有正式Trial/Confirm或持仓动作，仍以正式决策通知为准。"
        boundary = "A股开盘摘要是行情事实与结构总结，不直接替代MASTER买入/卖出链；集合竞价与连续竞价语义分开。"
    else:
        title = "【A股收盘｜即时摘要】全天结构与ETF强弱"
        implication = "收盘摘要用于快速确认全天指数结构、ETF横截面和日内路径结果；15:30的ETF交易复盘仍负责账户、风险许可、生命周期、资本效率和CASE的完整正式闭环。"
        action = "先看全天结构是否强化或破坏当前持仓/候选假设；完整交易结论以随后ETF交易复盘为准。"
        boundary = "收盘PushPlus不重复完整复盘，不根据单日涨跌机械生成买卖动作。"

    provider_as_of = max([str(x.get("as_of_beijing") or "") for x in rows if x.get("as_of_beijing")], default=str(snapshot.get("captured_at_beijing") or ""))
    if node == "CLOSE":
        market_as_of = f"{market_date} 15:00:00"
        time_lines = [
            f"- **价格有效时点**：{market_as_of}",
            f"- **provider补查/观测上限**：{provider_as_of or '未提供'}",
            f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
    else:
        market_as_of = provider_as_of
        time_lines = [f"- **A股行情依据**：{market_as_of or '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"]

    content = render_summary(
        headline_lines=[f"- **节点**：{'开盘后稳定观察' if node == 'OPEN' else '15:00正式收盘'}"] + headline,
        path_lines=path_lines,
        implication=implication,
        action=action,
        as_of_lines=time_lines,
        boundary=boundary,
    )
    return {
        "key": f"a-share-session-summary:{market_date}:{node}",
        "type": "A股节点总结",
        "event_type": "A_SHARE_SESSION_SUMMARY",
        "title": title,
        "content": content,
        "source": "CURRENT+intraday_path_features",
        "user_severity": "需要关注",
        "user_action": "查看结构摘要；正式交易动作以MASTER完整决策链为准",
        "confirmation_context": {"market_date": market_date, "session_node": node, "market_as_of_beijing": market_as_of, "provider_observed_as_of_beijing": provider_as_of},
    }


def _asia_return(obj: dict) -> float | None:
    latest = obj.get("latest") or {}
    close = number(latest.get("close"))
    prev = number(latest.get("previous_close"))
    if prev is None:
        prev = number(obj.get("previous_close_reference"))
    return pct_change(close, prev)


def _asia_event() -> dict | None:
    node = _bj_node("asia")
    if not node:
        return None
    context = read_json(OVERSEAS, {})
    objects = context.get("objects") or {}
    specs = [("N225", "日经225指数（N225）"), ("KOSPI", "韩国综合指数（KOSPI）"), ("TWII", "台湾加权指数（TWII）")]
    selected = []
    for code, label in specs:
        obj = objects.get(code) or {}
        if obj.get("quality_status") != "PASS":
            continue
        latest = obj.get("latest") or {}
        selected.append((code, label, obj, latest))
    if len(selected) < 2:
        return None

    returns = [x for x in (_asia_return(obj) for _, _, obj, _ in selected) if x is not None]
    avg = sum(returns) / len(returns) if returns else 0.0
    if returns and all(x > 0 for x in returns):
        tone = "区域普遍偏强"
    elif returns and all(x < 0 for x in returns):
        tone = "区域普遍偏弱"
    elif abs(avg) >= 1.0:
        tone = "区域分化但方向偏强" if avg > 0 else "区域分化但方向偏弱"
    else:
        tone = "区域分化/相对平稳"

    headline = [f"- **综合判断**：{tone}"]
    path_lines: list[str] = []
    dates: list[str] = []
    times: list[str] = []
    for code, label, obj, latest in selected:
        ret = _asia_return(obj)
        open_to_now = pct_change(number(latest.get("close")), number(latest.get("open")))
        headline.append(f"- **{label}**：{'较前收' + pct(ret) if ret is not None else '较开盘' + pct(open_to_now)}")
        path_lines.append(f"- **{label}**：{ohlc_path_phrase(number(latest.get('open')), number(latest.get('high')), number(latest.get('low')), number(latest.get('close')))}")
        dates.append(str(latest.get("market_date_local") or ""))
        if latest.get("as_of_beijing"):
            times.append(str(latest.get("as_of_beijing")))
    market_date = max([x for x in dates if x], default=datetime.now(BEIJING).date().isoformat())

    if node == "OPEN":
        title = f"【日韩台开盘｜A股盘前/早盘参考】{tone}"
        implication = "日韩台开盘结构提供同亚洲时区的外部风险偏好和科技链/出口链背景。它比上一夜美股更接近A股开盘时点，但仍必须观察A股集合竞价、连续竞价和ETF自身反馈是否同向。"
        action = "把区域方向和分化纳入A股开盘验证，不因日韩台单独涨跌机械追买或卖出A股ETF。"
        boundary = "区域开盘采用聚合通知，避免日本、韩国、台湾分别刷屏；香港尚未开盘，因此不在09:05摘要中伪造同步行情。"
    else:
        title = f"【日韩台收盘｜A股尾盘参考】{tone}"
        implication = "日韩台当天完整结果可用于检验亚洲风险偏好是否与A股日内反馈共振或背离；尤其关注A股科技/出口相关方向在区域市场收盘后是否仍保持独立强弱。"
        action = "把区域收盘结果纳入A股尾盘和收盘复核；如果与A股明显背离，优先检查本地独立驱动，而不是机械向海外靠拢。"
        boundary = "日韩台收盘总结只提供跨市场证据；香港仍在交易，将由独立香港收盘节点补齐，不生成A股交易权限或动作。"

    content = render_summary(
        headline_lines=[f"- **节点**：{'区域开盘聚合观察' if node == 'OPEN' else '区域收盘聚合总结'}"] + headline,
        path_lines=path_lines,
        implication=implication,
        action=action,
        as_of_lines=[f"- **各市场最新有效时点上限**：{max(times) if times else '未提供'}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
        boundary=boundary + "\n\n> 外部结构 → 本地传导 → ETF自身反馈 → 机会判断。",
    )
    return {
        "key": f"asia-session-summary:{market_date}:{node}",
        "type": "日韩台节点总结",
        "event_type": "ASIA_SESSION_SUMMARY",
        "title": title,
        "content": content,
        "source": "overseas_context",
        "user_severity": "需要关注",
        "user_action": "纳入A股开盘/尾盘跨市场验证，不机械交易",
        "confirmation_context": {"market_date": market_date, "session_node": node, "tone": tone, "market_as_of_beijing": max(times) if times else ""},
    }


def _hk_event() -> dict | None:
    node = _bj_node("hk")
    if not node:
        return None
    context = read_json(OVERSEAS, {})
    obj = (context.get("objects") or {}).get("HSTECH") or {}
    if obj.get("quality_status") != "PASS":
        return None
    latest = obj.get("latest") or {}
    market_date = str(latest.get("market_date_local") or "")
    as_of = str(latest.get("as_of_beijing") or "")
    if not market_date or not as_of:
        return None
    phase = str(obj.get("market_phase_at_generation") or "")
    if node == "OPEN" and phase not in {"OPEN", "REGULAR"}:
        return None
    if node == "CLOSE" and phase != "CLOSED":
        return None

    ret = _asia_return(obj)
    open_to_now = pct_change(number(latest.get("close")), number(latest.get("open")))
    tone_value = ret if ret is not None else open_to_now
    if tone_value is None:
        tone = "方向信息不足"
    elif tone_value >= 1.0:
        tone = "明显偏强"
    elif tone_value >= 0.3:
        tone = "偏强"
    elif tone_value <= -1.0:
        tone = "明显偏弱"
    elif tone_value <= -0.3:
        tone = "偏弱"
    else:
        tone = "相对平稳"

    metric = f"较前收{pct(ret)}" if ret is not None else f"较开盘{pct(open_to_now)}"
    headline = [
        f"- **综合判断**：{tone}",
        f"- **恒生科技指数（HSTECH）**：{metric}",
    ]
    path_lines = [f"- **恒生科技指数（HSTECH）**：{ohlc_path_phrase(number(latest.get('open')), number(latest.get('high')), number(latest.get('low')), number(latest.get('close')))}"]

    if node == "OPEN":
        title = f"【香港开盘｜A股同步参考】恒生科技{tone}"
        implication = "香港与A股基本同步开盘，恒生科技指数（HSTECH）能够补充中国科技风险偏好的跨市场反馈；重点检查其与科创、创业板、通信/半导体方向以及恒生科技ETF（513180）是否共振或背离。"
        action = "把香港开盘结构并入当前A股早盘判断；若A股与香港明显背离，优先寻找本地独立驱动，不因香港单一方向机械交易。"
        boundary = "香港09:30才开盘，因此不塞入09:05日韩台摘要；本节点独立保存真实provider时点，不把同步市场理解成完全同一秒。"
    else:
        title = f"【香港收盘｜次日A股参考】恒生科技{tone}"
        implication = "香港16:00收盘晚于A股，可作为当日中国科技风险偏好的最后一轮区域反馈；它尤其有助于判断A股收盘后，恒生科技ETF（513180）及相关科技风险因子的次日背景是否得到强化或削弱。"
        action = "把香港收盘结构纳入下一A股交易日盘前；无需在收盘后机械调整A股持仓，仍需次日A股本地反馈确认。"
        boundary = "香港收盘是A股收盘后的外部/区域反馈，不反向改写A股15:00已形成的正式收盘事实，也不直接生成A股交易动作。"

    content = render_summary(
        headline_lines=[f"- **节点**：{'香港开盘后稳定观察' if node == 'OPEN' else '香港正式收盘'}"] + headline,
        path_lines=path_lines,
        implication=implication,
        action=action,
        as_of_lines=[f"- **恒生科技指数行情依据**：{as_of}", f"- **通知生成**：{now().strftime('%Y-%m-%d %H:%M:%S')}"],
        boundary=boundary + "\n\n> 外部结构 → 本地传导 → ETF自身反馈 → 机会判断。",
    )
    return {
        "key": f"hk-session-summary:{market_date}:{node}",
        "type": "香港节点总结",
        "event_type": "HK_SESSION_SUMMARY",
        "title": title,
        "content": content,
        "source": "overseas_context:HSTECH",
        "security_code": "HSTECH",
        "security_name": "恒生科技指数",
        "user_severity": "需要关注",
        "user_action": "纳入A股同步/次日跨市场验证，不机械交易",
        "confirmation_context": {"market_date": market_date, "session_node": node, "tone": tone, "market_as_of_beijing": as_of},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a-share", "asia", "hk"], required=True)
    args = parser.parse_args()
    if args.market == "a-share":
        event = _a_share_event()
    elif args.market == "asia":
        event = _asia_event()
    else:
        event = _hk_event()
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(event, policy="A股、美股、日韩台、香港采用统一节点总结模板；按各自真实交易时段生成，避免伪造同步；总结只提供结构证据，不替代正式交易决策。")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
