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

# Attention thresholds only. They do not create MASTER trading rules.
# The CLI source selector is a data-routing detail; it is not a user-visible
# notification category and never determines whether an object deserves notice.
THRESHOLDS = {
    "a-share": {
        "index_extreme": 2.5,
        "etf_extreme": 4.0,
        "stock_extreme": 5.0,
        "sudden": 1.0,
        "stock_sudden": 2.0,
        "index_reversal_excursion": 1.5,
        "etf_reversal_excursion": 2.5,
        "stock_reversal_excursion": 4.0,
        "index_divergence": 2.0,
        "etf_divergence": 4.0,
    },
    "asia": {"index_extreme": 2.0, "sudden": 1.0, "reversal_excursion": 1.5, "divergence": 2.0},
    "us": {"index_extreme": 2.0, "stock_extreme": 5.0, "sudden": 1.0, "stock_sudden": 2.0, "divergence": 1.5},
}

CATEGORY_LABEL = {
    "EXTREME": "极端波动",
    "SUDDEN": "快速重定价",
    "REVERSAL": "方向反转",
    "DIVERGENCE": "显著分化",
}


def _derived_previous_close(close: float | None, day_change_pct: float | None) -> float | None:
    if close is None or day_change_pct is None or day_change_pct <= -99.9:
        return None
    return close / (1.0 + day_change_pct / 100.0)


def _candidate_score(category: str, strength: float) -> float:
    base = {"EXTREME": 4.0, "REVERSAL": 3.5, "SUDDEN": 3.0, "DIVERGENCE": 2.5}.get(category, 1.0)
    return base + max(0.0, strength)


def _recent_duplicate(event: dict) -> bool:
    state = read_json(STATE / "notification_center.json", {})
    code = str(event.get("security_code") or "")
    new_ctx = event.get("confirmation_context") or {}
    direction = str(new_ctx.get("direction") or "")
    category = str(new_ctx.get("event_category") or "")
    market_date = str(new_ctx.get("market_date") or "")
    for item in reversed(state.get("notifications") or []):
        if str(item.get("event_type") or "") not in {"MARKET_SHOCK_ALERT", "MARKET_VALUE_ALERT"}:
            continue
        if str(item.get("security_code") or "") != code:
            continue
        ctx = item.get("confirmation_context") or {}
        if str(ctx.get("direction") or "") != direction:
            continue
        old_category = str(ctx.get("event_category") or ctx.get("shock_severity") or "")
        old_market_date = str(ctx.get("market_date") or "")
        if category in {"EXTREME", "REVERSAL", "DIVERGENCE"} and old_category == category and market_date and old_market_date == market_date:
            return True
        stamp = parse_notification_time(item.get("sent_at") or item.get("created_at"))
        if not stamp or now() - stamp > timedelta(minutes=COOLDOWN_MINUTES):
            continue
        if category in {"EXTREME", "REVERSAL"} and old_category != category:
            return False
        return True
    return False


def _snapshot_rows() -> tuple[dict, list[dict]]:
    current = read_json(STATE / "CURRENT.json", {})
    path = ROOT / str(current.get("latest_snapshot") or "")
    snapshot = read_json(path, {}) if path.exists() else {}
    return snapshot, snapshot.get("rows") or []


def _event_title(label: str, category: str, *, day: float | None, sudden: float | None, interval_min: float | None) -> str:
    if category == "EXTREME":
        return f"【异动提醒】{label}出现极端波动 {pct(day)}"
    if category == "SUDDEN":
        interval = f"约{interval_min:.0f}分钟" if interval_min is not None else "短时间"
        return f"【异动提醒】{label}{interval}快速重定价 {pct(sudden)}"
    if category == "REVERSAL":
        return f"【异动提醒】{label}日内方向明显反转"
    return f"【异动提醒】{label}出现显著分化"


def _why(category: str) -> str:
    return {
        "EXTREME": "当前幅度已进入通知层的非常规区间，可能实质改变风险偏好、风险收益或资本比较。",
        "SUDDEN": "相邻有效行情脉冲发生快速重新定价，需要立即核对承接、扩散范围和相对强弱；离散脉冲不能恢复两次采样之间全部分钟路径。",
        "REVERSAL": "行情先形成足够大的单向日内幅度，随后大幅回吐或修复，原先的单向市场解释可能已经失效。",
        "DIVERGENCE": "相关对象之间的相对差已经扩大到值得单独关注的程度，不能再用单一市场方向概括全部机会和风险。",
    }.get(category, "该新事实足以改变当前注意力排序或下一节点准备。")


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
    candidates: list[dict] = []

    valid_rows = [r for r in rows if r.get("asset_class") in {"A_SHARE_INDEX", "ETF"} and number(r.get("change_pct")) is not None]
    for row in valid_rows:
        code = str(row.get("symbol") or "")
        asset = str(row.get("asset_class") or "")
        day = number(row.get("change_pct"))
        close = number(row.get("close"))
        if day is None:
            continue
        extreme_limit = THRESHOLDS["a-share"]["index_extreme" if asset == "A_SHARE_INDEX" else "etf_extreme"]
        if abs(day) >= extreme_limit:
            candidates.append({"score": _candidate_score("EXTREME", abs(day) / extreme_limit), "category": "EXTREME", "code": code, "day": day, "sudden": None, "row": row, "direction": "UP" if day >= 0 else "DOWN", "asset": asset})

        d = delta_map.get(code) or {}
        sudden = number(d.get("price_change_pct"))
        if sudden is not None and interval_min is not None and 0 < interval_min <= 20 and abs(sudden) >= THRESHOLDS["a-share"]["sudden"]:
            candidates.append({"score": _candidate_score("SUDDEN", abs(sudden) / THRESHOLDS["a-share"]["sudden"]), "category": "SUDDEN", "code": code, "day": day, "sudden": sudden, "row": row, "direction": "UP" if sudden >= 0 else "DOWN", "asset": asset})

        prev_close = _derived_previous_close(close, day)
        high_ret = pct_change(number(row.get("high")), prev_close)
        low_ret = pct_change(number(row.get("low")), prev_close)
        excursion = THRESHOLDS["a-share"]["index_reversal_excursion" if asset == "A_SHARE_INDEX" else "etf_reversal_excursion"]
        neutral = 0.3 if asset == "A_SHARE_INDEX" else 0.5
        if high_ret is not None and high_ret >= excursion and day <= neutral:
            candidates.append({"score": _candidate_score("REVERSAL", high_ret / excursion), "category": "REVERSAL", "code": code, "day": day, "sudden": sudden, "row": row, "direction": "DOWN", "asset": asset, "extra": f"盘中一度较前收{pct(high_ret)}，当前回落至{pct(day)}"})
        if low_ret is not None and low_ret <= -excursion and day >= -neutral:
            candidates.append({"score": _candidate_score("REVERSAL", abs(low_ret) / excursion), "category": "REVERSAL", "code": code, "day": day, "sudden": sudden, "row": row, "direction": "UP", "asset": asset, "extra": f"盘中一度较前收{pct(low_ret)}，当前修复至{pct(day)}"})

    # Account-confirmed A-share base stocks participate in the same attention
    # event engine.  Their thresholds are intentionally wider than index/ETF
    # thresholds because normal single-stock volatility is higher.
    stock_context = read_json(STATE / "stock_market_context.json", {})
    for code, obj in (stock_context.get("objects") or {}).items():
        if obj.get("quality_status") != "PASS" or number(obj.get("change_pct")) is None:
            continue
        as_of = parse_notification_time(obj.get("as_of_beijing"))
        if not as_of or as_of.date().isoformat() != market_date:
            continue
        day = number(obj.get("change_pct"))
        close = number(obj.get("close"))
        extreme_limit = THRESHOLDS["a-share"]["stock_extreme"]
        row = {
            "symbol": str(code),
            "provider_name": str(obj.get("name") or code),
            "asset_class": "ACCOUNT_STOCK",
            "change_pct": day,
            "close": close,
            "open": obj.get("open"),
            "high": obj.get("high"),
            "low": obj.get("low"),
            "as_of_beijing": obj.get("as_of_beijing"),
        }
        if day is not None and abs(day) >= extreme_limit:
            candidates.append({"score": _candidate_score("EXTREME", abs(day) / extreme_limit), "category": "EXTREME", "code": str(code), "day": day, "sudden": None, "row": row, "direction": "UP" if day >= 0 else "DOWN", "asset": "ACCOUNT_STOCK"})
        prev_close = number(obj.get("prev_close"))
        high_ret = pct_change(number(obj.get("high")), prev_close)
        low_ret = pct_change(number(obj.get("low")), prev_close)
        excursion = THRESHOLDS["a-share"]["stock_reversal_excursion"]
        if high_ret is not None and high_ret >= excursion and day is not None and day <= 1.0:
            candidates.append({"score": _candidate_score("REVERSAL", high_ret / excursion), "category": "REVERSAL", "code": str(code), "day": day, "sudden": None, "row": row, "direction": "DOWN", "asset": "ACCOUNT_STOCK", "extra": f"盘中一度较前收{pct(high_ret)}，当前回落至{pct(day)}"})
        if low_ret is not None and low_ret <= -excursion and day is not None and day >= -1.0:
            candidates.append({"score": _candidate_score("REVERSAL", abs(low_ret) / excursion), "category": "REVERSAL", "code": str(code), "day": day, "sudden": None, "row": row, "direction": "UP", "asset": "ACCOUNT_STOCK", "extra": f"盘中一度较前收{pct(low_ret)}，当前修复至{pct(day)}"})

    # Divergence remains a relationship event, so it is evaluated only inside
    # coherent comparison groups rather than across unrelated stocks and ETFs.
    index_rows = [r for r in valid_rows if r.get("asset_class") == "A_SHARE_INDEX"]
    etf_rows = [r for r in valid_rows if r.get("asset_class") == "ETF"]
    for group, limit, code, label in (
        (index_rows, THRESHOLDS["a-share"]["index_divergence"], "A_SHARE_INDEX_DIVERGENCE", "A股核心指数"),
        (etf_rows, THRESHOLDS["a-share"]["etf_divergence"], "A_SHARE_ETF_DIVERGENCE", "A股监测ETF横截面"),
    ):
        values = [(str(r.get("symbol") or ""), float(r.get("change_pct"))) for r in group if number(r.get("change_pct")) is not None]
        if len(values) >= 2:
            high = max(values, key=lambda x: x[1])
            low = min(values, key=lambda x: x[1])
            spread = high[1] - low[1]
            if spread >= limit:
                candidates.append({"score": _candidate_score("DIVERGENCE", spread / limit), "category": "DIVERGENCE", "code": code, "day": None, "sudden": None, "row": {}, "direction": "DIVERGED", "asset": "STRUCTURE", "name": label, "extra": f"领先{high[0]} {pct(high[1])}，落后{low[0]} {pct(low[1])}，差约{spread:.2f}个百分点"})

    if not candidates:
        return None
    c = max(candidates, key=lambda x: float(x.get("score") or 0))
    category = str(c["category"])
    code = str(c["code"])
    row = c.get("row") or {}
    day = number(c.get("day"))
    sudden = number(c.get("sudden"))
    direction = str(c.get("direction") or "")
    name = str(c.get("name") or row.get("provider_name") or code)
    label = name if code.startswith("A_SHARE_") else f"{name}（{code}）"
    as_of = str(row.get("as_of_beijing") or snapshot.get("captured_at_beijing") or "")

    what = [f"- **事件类型**：{CATEGORY_LABEL[category]}", f"- **对象/结构**：{label}"]
    if day is not None:
        what.append(f"- **当日涨跌**：{pct(day)}")
    if sudden is not None:
        what.append(f"- **最近脉冲变化**：{pct(sudden)}")
    if c.get("extra"):
        what.append(f"- **关键结构**：{c['extra']}")

    event = {
        "key": f"market-value:a-share:{market_date}:{code}:{direction}:{category}:{dt.strftime('%H')}:{dt.minute // 15}",
        "type": "市场有价值事件",
        "event_type": "MARKET_VALUE_ALERT",
        "title": _event_title(label, category, day=day, sudden=sudden, interval_min=interval_min),
        "content": render_shock(
            what=what,
            why=_why(category),
            implication="立即检查该新事实是否改变全部持仓ETF与观察ETF的相对强弱、风险收益、唯一主候选或资本效率；如果对象是账户底仓个股，还要检查它对资金释放和独立假设的影响。",
            action="打开ETF项目刷新当前正式判断；只有正式风险许可、机会状态、金额或持仓动作发生变化时才执行交易。",
            as_of=as_of or "未提供",
            boundary="本通知识别的是注意力事件，不属于MASTER交易规则，不新增风险许可、生命周期或金额档。市场归属只用于行情时点和传导解释，不决定通知资格。",
        ),
        "source": "market_delta+CURRENT+stock_market_context",
        "security_code": code,
        "security_name": name,
        "user_severity": "需要关注",
        "user_action": "刷新ETF正式判断，核对事件是否改变机会、持仓风险收益或下一节点准备",
        "confirmation_context": {"market": "A_SHARE", "market_date": market_date, "direction": direction, "event_category": category, "asset_class": c.get("asset"), "day_change_pct": day, "sudden_change_pct": sudden, "interval_minutes": interval_min, "market_as_of_beijing": as_of},
    }
    return None if _recent_duplicate(event) else event


def _context_candidate(source: str) -> dict | None:
    current_path = STATE / ("us_extended_hours_context.json" if source == "us" else "overseas_context.json")
    current = read_json(current_path, {})
    previous_path = Path(os.environ.get("PREVIOUS_CONTEXT_PATH", ""))
    previous = read_json(previous_path, {}) if previous_path.exists() else {}
    current_objects = current.get("objects") or {}
    previous_objects = previous.get("objects") or {}
    today_bj = datetime.now(BEIJING).date().isoformat()

    if source == "us":
        # Scan every object currently admitted to the US context. QQQ/SOXX are
        # the stable core proxies; conditional industry stocks can also notify
        # when they are present, but do not become a permanent stock pool.
        symbols = tuple(current_objects.keys())
    else:
        symbols = tuple(code for code in ("N225", "KOSPI", "TWII", "HSTECH") if code in current_objects)

    labels: dict[str, str] = {}
    returns: dict[str, float] = {}
    metric_labels: dict[str, str] = {}
    market_dates: dict[str, str] = {}
    candidates: list[dict] = []

    for code in symbols:
        obj = current_objects.get(code) or {}
        latest = obj.get("latest") or {}
        raw_name = str(obj.get("name") or code)
        labels[code] = f"{raw_name}（{code}）" if code not in raw_name else raw_name
        price_now = number(latest.get("close"))
        if price_now is None or obj.get("quality_status") not in {"PASS", "FRESH"}:
            continue
        market_dates[code] = str(latest.get("market_date_local") or obj.get("regular_session_market_date") or "")
        if source == "asia" and market_dates[code] != today_bj:
            continue

        is_core_us = code in {"QQQ", "SOXX"}
        if source == "us":
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
            extreme_limit = THRESHOLDS["us"]["index_extreme" if is_core_us else "stock_extreme"]
            sudden_limit = THRESHOLDS["us"]["sudden" if is_core_us else "stock_sudden"]
        else:
            prev_close = number(latest.get("previous_close")) or number(obj.get("previous_close_reference"))
            day = pct_change(price_now, prev_close)
            if day is None:
                day = pct_change(price_now, number(latest.get("open")))
                metric_labels[code] = "较开盘"
            else:
                metric_labels[code] = "较前收"
            extreme_limit = THRESHOLDS["asia"]["index_extreme"]
            sudden_limit = THRESHOLDS["asia"]["sudden"]

        if day is not None:
            if source != "us" or is_core_us:
                returns[code] = day
            if abs(day) >= extreme_limit:
                candidates.append({"score": _candidate_score("EXTREME", abs(day) / extreme_limit), "category": "EXTREME", "code": code, "day": day, "sudden": None, "latest": latest, "direction": "UP" if day >= 0 else "DOWN"})

        prev_obj = previous_objects.get(code) or {}
        prev_latest = prev_obj.get("latest") or {}
        price_prev = number(prev_latest.get("close"))
        t_now = parse_notification_time(latest.get("as_of_beijing"))
        t_prev = parse_notification_time(prev_latest.get("as_of_beijing"))
        sudden = None
        minutes = None
        if price_prev is not None and t_now and t_prev:
            minutes = (t_now - t_prev).total_seconds() / 60.0
            sudden = pct_change(price_now, price_prev)
            if 0 < minutes <= 20 and sudden is not None and abs(sudden) >= sudden_limit:
                candidates.append({"score": _candidate_score("SUDDEN", abs(sudden) / sudden_limit), "category": "SUDDEN", "code": code, "day": day, "sudden": sudden, "latest": latest, "direction": "UP" if sudden >= 0 else "DOWN", "minutes": minutes})

        if source == "asia" and day is not None:
            prev_close = number(latest.get("previous_close")) or number(obj.get("previous_close_reference"))
            high_ret = pct_change(number(latest.get("high")), prev_close)
            low_ret = pct_change(number(latest.get("low")), prev_close)
            excursion = THRESHOLDS["asia"]["reversal_excursion"]
            if high_ret is not None and high_ret >= excursion and day <= 0.3:
                candidates.append({"score": _candidate_score("REVERSAL", high_ret / excursion), "category": "REVERSAL", "code": code, "day": day, "sudden": sudden, "latest": latest, "direction": "DOWN", "extra": f"盘中一度较前收{pct(high_ret)}，当前回落至{pct(day)}"})
            if low_ret is not None and low_ret <= -excursion and day >= -0.3:
                candidates.append({"score": _candidate_score("REVERSAL", abs(low_ret) / excursion), "category": "REVERSAL", "code": code, "day": day, "sudden": sudden, "latest": latest, "direction": "UP", "extra": f"盘中一度较前收{pct(low_ret)}，当前修复至{pct(day)}"})

    divergence_limit = THRESHOLDS[source]["divergence"]
    if len(returns) >= 2:
        high = max(returns.items(), key=lambda x: x[1])
        low = min(returns.items(), key=lambda x: x[1])
        spread = high[1] - low[1]
        if spread >= divergence_limit:
            synthetic_code = "US_TECH_DIVERGENCE" if source == "us" else "APAC_DIVERGENCE"
            candidates.append({"score": _candidate_score("DIVERGENCE", spread / divergence_limit), "category": "DIVERGENCE", "code": synthetic_code, "day": None, "sudden": None, "latest": {}, "direction": "DIVERGED", "name": "美股科技内部结构" if source == "us" else "亚太主要指数结构", "extra": f"领先{labels[high[0]]} {pct(high[1])}，落后{labels[low[0]]} {pct(low[1])}，差约{spread:.2f}个百分点"})

    if not candidates:
        return None
    c = max(candidates, key=lambda x: float(x.get("score") or 0))
    category = str(c["category"])
    code = str(c["code"])
    day = number(c.get("day"))
    sudden = number(c.get("sudden"))
    latest = c.get("latest") or {}
    direction = str(c.get("direction") or "")
    label = str(c.get("name") or labels.get(code) or code)
    market_date = market_dates.get(code) or max([x for x in market_dates.values() if x], default=today_bj)
    as_of = str(latest.get("as_of_beijing") or current.get("generated_at_beijing") or "")
    minutes = number(c.get("minutes"))

    what = [f"- **事件类型**：{CATEGORY_LABEL[category]}", f"- **对象/结构**：{label}"]
    if day is not None:
        what.append(f"- **{metric_labels.get(code, '当前阶段变化')}**：{pct(day)}")
    if sudden is not None:
        what.append(f"- **最近脉冲变化**：{pct(sudden)}")
    if c.get("extra"):
        what.append(f"- **关键结构**：{c['extra']}")

    dt = datetime.now(BEIJING)
    event = {
        "key": f"market-value:{source}:{market_date}:{code}:{direction}:{category}:{dt.strftime('%H')}:{dt.minute // 15}",
        "type": "市场有价值事件",
        "event_type": "MARKET_VALUE_ALERT",
        "title": _event_title(label, category, day=day, sudden=sudden, interval_min=minutes),
        "content": render_shock(
            what=what,
            why=_why(category),
            implication="把本次新事实作为结构证据，检查它是否改变A股风险传导、相关ETF自身反馈、候选比较或下一节点准备；恒生科技指数（HSTECH）事件还要直接复核恒生科技ETF（513180）的自身反馈。",
            action="无需因单一海外对象机械调整A股持仓；在最近有效A股决策节点重新完成外部结构→本地传导→ETF自身反馈→机会判断。",
            as_of=as_of or "未提供",
            boundary="市场归属只用于数据时点和传导解释，不决定通知资格。事件通知只提高关注优先级，不直接生成A股风险许可、金额或卖出动作；美股扩展时段与现金盘语义继续严格分开。",
        ),
        "source": str(current_path.relative_to(ROOT)),
        "security_code": code,
        "security_name": label.split("（")[0],
        "user_severity": "需要关注",
        "user_action": "纳入最近A股决策节点重新验证，不机械交易",
        "confirmation_context": {"market": source.upper(), "market_date": market_date, "direction": direction, "event_category": category, "phase_metric_change_pct": day, "sudden_change_pct": sudden, "market_as_of_beijing": as_of},
    }
    return None if _recent_duplicate(event) else event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a-share", "asia", "us"], required=True, help="data source route only; not a notification category")
    args = parser.parse_args()
    event = _a_share_candidate() if args.market == "a-share" else _context_candidate(args.market)
    if not event:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    result = persist_and_send(
        event,
        policy="主动通知按新事实的决策价值判断，不按市场分类；统一【异动提醒】标题，当前事件族为开盘异常、快速重定价、极端波动、方向反转、显著分化；任何当前正式监测指数、ETF或条件/账户个股均可参与，阈值只控制注意力。",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "CREATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
