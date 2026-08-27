from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from market_notification_common import number, pct, pct_change, persist_and_send, render_shock
from notification_center import STATE, now, parse_notification_time, read_json

ROOT = Path(__file__).resolve().parents[1]
BEIJING = ZoneInfo("Asia/Shanghai")
COOLDOWN_MINUTES = 45
# Notification thresholds only. They are attention filters, not MASTER trading rules.
THRESHOLDS = {
    "a-share": {"index_extreme": 2.5, "etf_extreme": 4.0, "sudden": 1.0},
    "asia": {"index_extreme": 2.0, "sudden": 1.0},
    "us": {"index_extreme": 2.0, "sudden": 1.0},
}


def _recent_duplicate(event: dict) -> bool:
    state = read_json(STATE / "notification_center.json", {})
    code = str(event.get("security_code") or "")
    new_ctx = event.get("confirmation_context") or {}
    direction = str(new_ctx.get("direction") or "")
    severity = str(new_ctx.get("shock_severity") or "")
    market_date = str(new_ctx.get("market_date") or "")
    for item in reversed(state.get("notifications") or []):
        if str(item.get("event_type") or "") != "MARKET_SHOCK_ALERT":
            continue
        if str(item.get("security_code") or "") != code:
            continue
        ctx = item.get("confirmation_context") or {}
        if str(ctx.get("direction") or "") != direction:
            continue
        old_severity = str(ctx.get("shock_severity") or "")
        old_market_date = str(ctx.get("market_date") or "")
        # A persistent extreme condition is one event per market day. A prior
        # SUDDEN alert may still escalate once to EXTREME.
        if severity == "EXTREME" and old_severity == "EXTREME" and market_date and old_market_date == market_date:
            return True
        stamp = parse_notification_time(item.get("sent_at") or item.get("created_at"))
        if not stamp or now() - stamp > timedelta(minutes=COOLDOWN_MINUTES):
            continue
        if severity == "EXTREME" and old_severity != "EXTREME":
            return False
        return True
    return False


def _snapshot_rows() -> tuple[dict, list[dict]]:
    current = read_json(STATE / "CURRENT.json", {})
    path = ROOT / str(current.get("latest_snapshot") or "")
    snapshot = read_json(path, {}) if path.exists() else {}
    return snapshot, snapshot.get("rows") or []


def _a_share_candidate() -> dict | None:
    dt = datetime.now(BEIJING)
    minute = dt.hour * 60 + dt.minute
    if not (9 * 60 + 30 <= minute <= 15 * 60 + 30):
        return None
    snapshot, rows = _snapshot_rows()
    if not rows:
        return None
    market_date = str(snapshot.get("market_date") or "")
    if market_date != dt.date().isoformat():
        return None
    if minute >= 15 * 60 and str(snapshot.get("node") or "") != "close":
        return None

    delta = read_json(STATE / "market_delta.json", {})
    interval_min = number(delta.get("interval_seconds"))
    interval_min = interval_min / 60.0 if interval_min is not None else None
    delta_map = {str(r.get("symbol") or ""): r for r in (delta.get("changes") or [])}
    candidates = []
    for row in rows:
        code = str(row.get("symbol") or "")
        asset = str(row.get("asset_class") or "")
        day = number(row.get("change_pct"))
        if day is None:
            continue
        limit = THRESHOLDS["a-share"]["index_extreme" if asset == "A_SHARE_INDEX" else "etf_extreme"]
        if abs(day) >= limit:
            candidates.append((abs(day) / limit + 1.0, "EXTREME", code, day, None, row))
        d = delta_map.get(code) or {}
        sudden = number(d.get("price_change_pct"))
        if sudden is not None and interval_min is not None and 0 < interval_min <= 20 and abs(sudden) >= THRESHOLDS["a-share"]["sudden"]:
            candidates.append((abs(sudden) / THRESHOLDS["a-share"]["sudden"], "SUDDEN", code, day, sudden, row))
    if not candidates:
        return None

    _, severity, code, day, sudden, row = max(candidates, key=lambda x: x[0])
    name = str(row.get("provider_name") or code)
    label = f"{name}（{code}）"
    move = sudden if severity == "SUDDEN" and sudden is not None else day
    direction = "UP" if move >= 0 else "DOWN"
    if severity == "EXTREME":
        title = f"【极端涨跌｜需关注】{label}当日{pct(day)}"
        why = "当前涨跌幅已达到通知层的极端波动阈值，可能改变市场风险偏好、相关ETF相对强弱或持仓风险收益，值得立即重新观察。"
    else:
        title = f"【突然涨跌｜需关注】{label}约{interval_min:.0f}分钟{pct(sudden)}"
        why = "相邻有效行情脉冲出现快速价格变化，属于需要立即核对承接和相对强弱的异动；离散脉冲不能恢复两次采样之间全部分钟路径。"
    as_of = str(row.get("as_of_beijing") or snapshot.get("captured_at_beijing") or "")
    event = {
        "key": f"market-shock:a-share:{market_date}:{code}:{direction}:{severity}:{dt.strftime('%H')}:{dt.minute // 15}",
        "type": "市场异动",
        "event_type": "MARKET_SHOCK_ALERT",
        "title": title,
        "content": render_shock(
            what=[f"- **对象**：{label}", f"- **当日涨跌**：{pct(day)}", f"- **相邻脉冲变化**：{pct(sudden) if sudden is not None else '未触发'}"],
            why=why,
            implication="立即检查该异动是否扩散到相关指数、持仓ETF和观察ETF，以及是否改变当前唯一主候选、持仓风险收益或资本效率；异动本身不能直接产生买卖动作。",
            action="打开ETF项目刷新当前正式判断；如没有正式风险许可/机会/卖出动作变化，不因单一异动机械交易。",
            as_of=as_of or "未提供",
            boundary="阈值只控制是否值得微信提醒，不属于MASTER交易规则，也不新增风险许可或生命周期等级。",
        ),
        "source": "market_delta+CURRENT",
        "security_code": code,
        "security_name": name,
        "user_severity": "需要关注",
        "user_action": "刷新ETF正式判断，核对异动是否改变机会或风险收益",
        "confirmation_context": {"market": "A_SHARE", "market_date": market_date, "direction": direction, "shock_severity": severity, "day_change_pct": day, "sudden_change_pct": sudden, "interval_minutes": interval_min, "market_as_of_beijing": as_of},
    }
    return None if _recent_duplicate(event) else event


def _context_candidate(market: str) -> dict | None:
    current_path = STATE / ("us_extended_hours_context.json" if market == "us" else "overseas_context.json")
    current = read_json(current_path, {})
    previous_path = Path(os.environ.get("PREVIOUS_CONTEXT_PATH", ""))
    previous = read_json(previous_path, {}) if previous_path.exists() else {}
    current_objects = current.get("objects") or {}
    previous_objects = previous.get("objects") or {}
    symbols = ("QQQ", "SOXX") if market == "us" else ("N225", "KOSPI", "TWII")
    labels = {
        "QQQ": "纳指100ETF代理（QQQ）", "SOXX": "半导体ETF代理（SOXX）",
        "N225": "日经225指数（N225）", "KOSPI": "韩国综合指数（KOSPI）", "TWII": "台湾加权指数（TWII）",
    }
    candidates = []
    metric_labels: dict[str, str] = {}
    market_dates: dict[str, str] = {}
    for code in symbols:
        obj = current_objects.get(code) or {}
        latest = obj.get("latest") or {}
        price_now = number(latest.get("close"))
        if price_now is None or obj.get("quality_status") not in {"PASS", "FRESH"}:
            continue
        market_dates[code] = str(latest.get("market_date_local") or obj.get("regular_session_market_date") or "")
        if market == "us":
            phase = str(obj.get("current_market_phase") or "")
            if phase == "REGULAR":
                day = number(obj.get("regular_session_change_vs_previous_close_pct"))
                metric_labels[code] = "现金盘较前收"
            elif phase in {"PRE_MARKET", "POST_MARKET"}:
                day = number(obj.get("extended_change_vs_regular_close_pct"))
                metric_labels[code] = "扩展时段较最近现金盘收盘"
            else:
                day = None
                metric_labels[code] = "当前阶段"
        else:
            prev_close = number(latest.get("previous_close")) or number(obj.get("previous_close_reference"))
            day = pct_change(price_now, prev_close)
            metric_labels[code] = "较前收"
        if day is not None and abs(day) >= THRESHOLDS[market]["index_extreme"]:
            candidates.append((abs(day) / THRESHOLDS[market]["index_extreme"] + 1.0, "EXTREME", code, day, None, latest))
        prev_obj = previous_objects.get(code) or {}
        prev_latest = prev_obj.get("latest") or {}
        price_prev = number(prev_latest.get("close"))
        t_now = parse_notification_time(latest.get("as_of_beijing"))
        t_prev = parse_notification_time(prev_latest.get("as_of_beijing"))
        if price_prev is not None and t_now and t_prev:
            minutes = (t_now - t_prev).total_seconds() / 60.0
            sudden = pct_change(price_now, price_prev)
            if 0 < minutes <= 20 and sudden is not None and abs(sudden) >= THRESHOLDS[market]["sudden"]:
                candidates.append((abs(sudden) / THRESHOLDS[market]["sudden"], "SUDDEN", code, day, sudden, latest))
    if not candidates:
        return None

    _, severity, code, day, sudden, latest = max(candidates, key=lambda x: x[0])
    label = labels[code]
    move = sudden if severity == "SUDDEN" and sudden is not None else (day or 0.0)
    direction = "UP" if move >= 0 else "DOWN"
    market_name = "美股" if market == "us" else "日韩台"
    market_date = market_dates.get(code) or datetime.now(BEIJING).date().isoformat()
    title = f"【{'极端涨跌' if severity == 'EXTREME' else '突然涨跌'}｜需关注】{label}{pct(move)}"
    why = "涨跌达到海外通知层的极端阈值，可能改变下一A股交易节点的外部风险背景。" if severity == "EXTREME" else "连续海外脉冲之间出现快速变化，说明外部风险偏好正在短时间重新定价。"
    as_of = str(latest.get("as_of_beijing") or current.get("generated_at_beijing") or "")
    dt = datetime.now(BEIJING)
    event = {
        "key": f"market-shock:{market}:{market_date}:{code}:{direction}:{severity}:{dt.strftime('%H')}:{dt.minute // 15}",
        "type": "市场异动",
        "event_type": "MARKET_SHOCK_ALERT",
        "title": title,
        "content": render_shock(
            what=[f"- **市场**：{market_name}", f"- **对象**：{label}", f"- **{metric_labels.get(code, '当前阶段变化')}**：{pct(day)}", f"- **最近脉冲变化**：{pct(sudden) if sudden is not None else '未触发'}"],
            why=why,
            implication="把本次异动作为外部结构的新证据，观察是否向A股科技、风险偏好或相关ETF传导；如果A股尚未开盘，则纳入下一盘前；如果A股正在交易，则立即检查本地是否共振或背离。",
            action="无需因海外单一异动机械调整A股持仓；在最近有效A股决策节点重新完成外部结构→本地传导→ETF自身反馈→机会判断。",
            as_of=as_of or "未提供",
            boundary="海外异动通知只提高关注优先级，不直接生成A股风险许可、金额或卖出动作；美股盘前/盘后不得重复使用上一现金盘旧涨跌充当当前异动。",
        ),
        "source": str(current_path.relative_to(ROOT)),
        "security_code": code,
        "security_name": labels[code].split("（")[0],
        "user_severity": "需要关注",
        "user_action": "纳入最近A股决策节点重新验证，不机械交易",
        "confirmation_context": {"market": market.upper(), "market_date": market_date, "direction": direction, "shock_severity": severity, "phase_metric_change_pct": day, "sudden_change_pct": sudden, "market_as_of_beijing": as_of},
    }
    return None if _recent_duplicate(event) else event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a-share", "asia", "us"], required=True)
    args = parser.parse_args()
    event = _a_share_candidate() if args.market == "a-share" else _context_candidate(args.market)
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(event, policy="极端/突然涨跌采用统一异动模板；SUDDEN同对象同方向45分钟冷却；EXTREME同一市场日只发一次，SUDDEN升级EXTREME允许追加；阈值仅控制注意力，不属于交易规则。")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
