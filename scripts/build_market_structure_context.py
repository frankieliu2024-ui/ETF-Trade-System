from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

try:
    from state_manager import now_utc, read_current
except ModuleNotFoundError:
    from scripts.state_manager import now_utc, read_current

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY_DIR = Path("events/research/daily_features")


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct(new, old):
    new_v, old_v = safe_float(new), safe_float(old)
    if new_v is None or old_v in (None, 0.0):
        return None
    return (new_v / old_v - 1.0) * 100.0


def round4(value):
    return None if value is None else round(float(value), 4)


def parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def load_universe(root: Path) -> dict[str, str]:
    cfg = load_json(root / "config/market/etf_monitor_universe.json", {})
    return {str(x.get("code")): str(x.get("name")) for x in (cfg.get("objects") or []) if x.get("code")}


def latest_snapshot(root: Path, current: dict) -> tuple[str, dict]:
    rel = str(current.get("latest_snapshot") or "")
    path = root / rel if rel else None
    if path and path.exists():
        return rel, load_json(path, {})
    market_date = str(current.get("market_date") or "")
    candidates = sorted((root / "data/market/snapshots").glob(f"{market_date}_*.json")) if market_date else []
    if not candidates:
        return "", {}
    path = candidates[-1]
    return str(path.relative_to(root)).replace("\\", "/"), load_json(path, {})


def historical_rows(root: Path, code: str, market_date: str, limit: int = 120) -> list[dict]:
    rows = []
    base = root / DAILY_DIR
    if not base.exists():
        return rows
    for path in sorted(base.glob("*.json"), reverse=True):
        obj = load_json(path, {})
        d = str(obj.get("market_date") or "")
        if not d or d >= market_date:
            continue
        row = next((x for x in (obj.get("features") or []) if str(x.get("code")) == code), None)
        if row and safe_float(row.get("close")) is not None:
            rows.append({"market_date": d, **row})
            if len(rows) >= limit:
                break
    rows.reverse()
    return rows


def range_position(price, low, high):
    p, lo, hi = safe_float(price), safe_float(low), safe_float(high)
    if p is None or lo is None or hi is None or hi == lo:
        return None
    return (p - lo) / (hi - lo)


def _trend_state(closes: list[float], zone: str, ret5, ret20) -> tuple[str, str, dict]:
    if len(closes) < 10:
        return "TREND_UNAVAILABLE", "趋势样本不足", {}
    prior = closes[-10:-5]
    recent = closes[-5:]
    prior_high, prior_low = max(prior), min(prior)
    recent_high, recent_low = max(recent), min(recent)
    high_change = pct(recent_high, prior_high)
    low_change = pct(recent_low, prior_low)
    higher_high = high_change is not None and high_change > 0
    higher_low = low_change is not None and low_change > 0
    lower_high = high_change is not None and high_change < 0
    lower_low = low_change is not None and low_change < 0

    if higher_high and higher_low and (ret20 or 0) > 0:
        state, cn = "RISING_TREND", "近期高点与低点同步抬升，偏上升趋势"
    elif lower_high and lower_low and (ret20 or 0) < 0:
        state, cn = "FALLING_TREND", "近期高点与低点同步下移，偏下降趋势"
    elif zone == "HISTORICAL_LOW_ZONE" and higher_low and (ret5 or 0) > 0:
        state, cn = "LOW_ZONE_BASE_RECOVERY", "历史低位区出现低点抬升，偏筑底后的修复"
    elif zone == "HISTORICAL_HIGH_ZONE" and (ret20 or 0) > 0 and not lower_low:
        state, cn = "HIGH_ZONE_EXTENSION", "历史高位区仍维持正向趋势，偏高位延续"
    elif higher_low and lower_high:
        state, cn = "RANGE_COMPRESSION", "近期高低点收敛，偏区间压缩/整理"
    else:
        state, cn = "MIXED_OR_RANGE", "近期趋势结构混合，尚不能定义为单边趋势"
    return state, cn, {
        "recent_5_high_vs_prior_5_high_pct": round4(high_change),
        "recent_5_low_vs_prior_5_low_pct": round4(low_change),
        "higher_high": higher_high,
        "higher_low": higher_low,
        "lower_high": lower_high,
        "lower_low": lower_low,
    }


def history_context(rows: list[dict], current_price: float | None) -> dict:
    if current_price is None or not rows:
        return {
            "status": "HISTORY_UNAVAILABLE",
            "sample_count": len(rows),
            "historical_zone": "UNKNOWN",
            "trend_state": "TREND_UNAVAILABLE",
            "interpretation": "缺少足够历史日线，不判断当前历史位置或趋势。",
        }
    closes = [safe_float(x.get("close")) for x in rows]
    closes = [x for x in closes if x is not None]
    if not closes:
        return {"status": "HISTORY_UNAVAILABLE", "sample_count": 0, "historical_zone": "UNKNOWN", "trend_state": "TREND_UNAVAILABLE"}

    def window(n: int):
        vals = closes[-n:]
        if not vals:
            return {}
        lo, hi = min(vals), max(vals)
        return {
            "sample_count": len(vals),
            "low": round4(lo),
            "high": round4(hi),
            "position": round4(range_position(current_price, lo, hi)),
            "distance_to_high_pct": round4(pct(current_price, hi)),
            "distance_to_low_pct": round4(pct(current_price, lo)),
        }

    w20 = window(20)
    w60 = window(60)
    pos = w60.get("position") if w60.get("sample_count", 0) >= 20 else w20.get("position")
    if pos is None:
        zone = "UNKNOWN"
    elif pos >= 0.8:
        zone = "HISTORICAL_HIGH_ZONE"
    elif pos <= 0.2:
        zone = "HISTORICAL_LOW_ZONE"
    else:
        zone = "HISTORICAL_MID_ZONE"

    def lag_return(n: int):
        if len(closes) < n:
            return None
        return round4(pct(current_price, closes[-n]))

    ret5, ret20 = lag_return(5), lag_return(20)
    trend_state, trend_cn, trend_detail = _trend_state(closes, zone, ret5, ret20)
    zone_cn = {
        "HISTORICAL_HIGH_ZONE": "历史高位区",
        "HISTORICAL_LOW_ZONE": "历史低位区",
        "HISTORICAL_MID_ZONE": "历史中位区",
        "UNKNOWN": "历史位置未知",
    }[zone]
    return {
        "status": "READY",
        "sample_count": len(closes),
        "latest_history_date": rows[-1].get("market_date"),
        "return_vs_5_sessions_ago_pct": ret5,
        "return_vs_20_sessions_ago_pct": ret20,
        "window_20": w20,
        "window_60": w60,
        "historical_zone": zone,
        "trend_state": trend_state,
        "trend_interpretation_cn": trend_cn,
        "trend_detail": trend_detail,
        "interpretation": f"按可用历史日线，当前处于{zone_cn}；{trend_cn}。历史位置与趋势必须共同解释，不能把高位延续、低位反弹和筑底修复视为同一种强势。",
    }


def _trading_progress(as_of_beijing) -> float | None:
    dt = parse_dt(as_of_beijing)
    if dt is None:
        return None
    mins = dt.hour * 60 + dt.minute + dt.second / 60.0
    if mins < 570:
        return 0.0
    if mins <= 690:
        elapsed = mins - 570
    elif mins < 780:
        elapsed = 120.0
    elif mins <= 900:
        elapsed = 120.0 + mins - 780
    else:
        elapsed = 240.0
    return max(0.0, min(1.0, elapsed / 240.0))


def turnover_context(rows: list[dict], row: dict, intraday: dict) -> dict:
    hist_amounts = [safe_float(x.get("amount")) for x in rows[-20:]]
    hist_amounts = [x for x in hist_amounts if x is not None and x > 0]
    current_amount = safe_float(row.get("amount"))
    progress = _trading_progress(row.get("as_of_beijing"))
    avg20 = sum(hist_amounts) / len(hist_amounts) if hist_amounts else None
    pace = None
    if current_amount is not None and avg20 not in (None, 0.0) and progress not in (None, 0.0):
        pace = current_amount / (avg20 * progress)
    if pace is None:
        pace_state, pace_cn = "PACE_UNAVAILABLE", "成交进度证据不足"
    elif pace >= 1.25:
        pace_state, pace_cn = "ABOVE_NORMAL_PACE", "成交进度明显快于近20日常态"
    elif pace <= 0.75:
        pace_state, pace_cn = "BELOW_NORMAL_PACE", "成交进度明显慢于近20日常态"
    else:
        pace_state, pace_cn = "NORMAL_PACE", "成交进度接近近20日常态"

    ret = safe_float(row.get("change_pct")) or 0.0
    day_pos = safe_float(intraday.get("day_range_position"))
    slope = safe_float(intraday.get("recent_slope_pct_per_10m"))
    if pace is not None and pace >= 1.25 and ret > 0 and (day_pos or 0) >= 0.6 and (slope is None or slope >= -0.03):
        behavior, behavior_cn = "VOLUME_SUPPORTED_STRENGTH", "偏放量承接/强势维持"
    elif pace is not None and pace >= 1.25 and ((day_pos is not None and day_pos <= 0.4) or slope is not None and slope < -0.05):
        behavior, behavior_cn = "HIGH_VOLUME_WEAK_RESPONSE", "偏放量滞涨或放量回撤"
    elif pace is not None and pace <= 0.75 and ret > 0:
        behavior, behavior_cn = "LOW_VOLUME_RISE", "偏缩量上涨，承接强度仍需验证"
    elif pace is not None and pace <= 0.75 and ret < 0:
        behavior, behavior_cn = "LOW_VOLUME_DECLINE", "偏缩量回落"
    else:
        behavior, behavior_cn = "TURNOVER_NEUTRAL", "成交承接暂未形成明显异常"
    return {
        "status": "READY" if pace is not None else "DEGRADED",
        "as_of_beijing": row.get("as_of_beijing"),
        "trading_session_progress": round4(progress),
        "current_amount": round4(current_amount),
        "historical_20d_average_full_day_amount": round4(avg20),
        "time_normalized_amount_pace_ratio": round4(pace),
        "pace_state": pace_state,
        "pace_interpretation_cn": pace_cn,
        "acceptance_behavior": behavior,
        "acceptance_interpretation_cn": behavior_cn,
        "method_note": "用当前累计成交额除以近20日平均全天成交额，再按A股连续竞价已进行时间比例归一化；仅作轻量承接证据，不替代分钟量价。",
    }


def intraday_context(row: dict, path: dict) -> dict:
    price = safe_float(row.get("close"))
    day_low = safe_float(row.get("low"))
    day_high = safe_float(row.get("high"))
    day_pos = range_position(price, day_low, day_high)
    recovery = safe_float(path.get("recovery_from_path_low_pct"))
    retreat = safe_float(path.get("retreat_from_path_high_pct"))
    slope = safe_float(path.get("recent_slope_pct_per_10m"))
    path_change = safe_float(path.get("path_change_pct"))
    candidates = [str(x.get("label")) for x in (path.get("structure_candidates") or []) if x.get("label")]
    low_time = path.get("path_low_as_of_beijing")
    high_time = path.get("path_high_as_of_beijing")
    low_dt, high_dt = parse_dt(low_time), parse_dt(high_time)
    if low_dt and high_dt and low_dt < high_dt:
        extreme_sequence = "LOW_THEN_HIGH"
        sequence_cn = "先出现路径低点、后出现路径高点"
    elif low_dt and high_dt and high_dt < low_dt:
        extreme_sequence = "HIGH_THEN_LOW"
        sequence_cn = "先出现路径高点、后出现路径低点"
    elif low_dt and high_dt:
        extreme_sequence = "SAME_OR_UNRESOLVED"
        sequence_cn = "高低点时序无法区分"
    else:
        extreme_sequence = "UNAVAILABLE"
        sequence_cn = "缺少高低点时序"

    sampling = (path.get("sampling") or {}).get("coverage")
    v_candidate = (
        "V_RECOVERY_CANDIDATE" in candidates
        or (extreme_sequence == "LOW_THEN_HIGH" and (recovery or 0) >= 0.3 and (day_pos or 0) >= 0.6 and (slope or 0) >= 0)
    )
    if v_candidate:
        morphology = "V_OR_LOW_POINT_RECOVERY_CANDIDATE"
        cn = "存在低点修复/V型反转候选"
        confidence = "CONFIRMED" if sampling == "HIGH" else "CANDIDATE"
    elif day_pos is not None and day_pos >= 0.75 and (slope is None or slope >= -0.03):
        morphology = "HIGH_ZONE_HOLD"
        cn = "更接近当日高位震荡/强势维持"
        confidence = "DESCRIPTIVE"
    elif extreme_sequence == "HIGH_THEN_LOW" and ((retreat or 0) <= -0.3 or (day_pos is not None and day_pos <= 0.45)):
        morphology = "FADE_FROM_HIGH"
        cn = "更接近冲高回落/高位回撤"
        confidence = "DESCRIPTIVE"
    elif (retreat is not None and retreat <= -0.5) or (day_pos is not None and day_pos <= 0.35 and (slope or 0) < 0):
        morphology = "FADE_FROM_HIGH"
        cn = "更接近冲高回落/高位回撤"
        confidence = "DESCRIPTIVE"
    elif day_pos is not None and day_pos <= 0.25:
        morphology = "LOW_ZONE_WEAK"
        cn = "更接近当日低位弱势"
        confidence = "DESCRIPTIVE"
    else:
        morphology = "MID_RANGE_OR_UNRESOLVED"
        cn = "处于当日区间中部或结构尚未确认"
        confidence = "UNRESOLVED"

    return {
        "morphology": morphology,
        "morphology_confidence": confidence,
        "interpretation_cn": cn,
        "extreme_sequence": extreme_sequence,
        "extreme_sequence_interpretation_cn": sequence_cn,
        "path_low_as_of_beijing": low_time,
        "path_high_as_of_beijing": high_time,
        "day_range_position": round4(day_pos),
        "open_return_pct": round4(pct(row.get("open"), row.get("prev_close"))),
        "current_return_pct": round4(row.get("change_pct")),
        "path_change_pct": round4(path_change),
        "recovery_from_path_low_pct": round4(recovery),
        "retreat_from_path_high_pct": round4(retreat),
        "recent_slope_pct_per_10m": round4(slope),
        "sampling_coverage": sampling,
        "structure_candidates": candidates,
        "sampling_limitation": (path.get("sampling") or {}).get("limitation"),
        "interpretation_boundary": "离散采样为MEDIUM或更低时，V型/突破只称候选，不声称已确认分钟级形态。",
    }


def combined_interpretation(history: dict, intraday: dict, turnover: dict, current_return) -> str:
    ret = safe_float(current_return)
    zone = history.get("historical_zone")
    trend_cn = history.get("trend_interpretation_cn") or "趋势状态未知"
    if history.get("status") != "READY":
        history_phrase = "历史位置与趋势证据不足"
    elif zone == "HISTORICAL_HIGH_ZONE" and (ret or 0) > 0:
        history_phrase = f"上涨发生在历史高位区；{trend_cn}"
    elif zone == "HISTORICAL_LOW_ZONE" and (ret or 0) > 0:
        history_phrase = f"上涨发生在历史低位区；{trend_cn}，需验证是否从反弹升级为趋势"
    elif zone == "HISTORICAL_HIGH_ZONE" and (ret or 0) < 0:
        history_phrase = f"回落发生在历史高位区；{trend_cn}，需区分健康整理与趋势破坏"
    elif zone == "HISTORICAL_LOW_ZONE" and (ret or 0) < 0:
        history_phrase = f"下跌发生在历史低位区；{trend_cn}，赔率可能改善但承接仍弱"
    else:
        history_phrase = f"当前位于历史中位区；{trend_cn}"
    intraday_phrase = intraday.get("interpretation_cn") or "日内结构未确认"
    turnover_phrase = turnover.get("acceptance_interpretation_cn") or "成交承接证据不足"
    return f"{history_phrase}；日内{intraday_phrase}；成交侧{turnover_phrase}。"


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    snapshot_path, snapshot = latest_snapshot(root, current)
    market_date = str(snapshot.get("market_date") or current.get("market_date") or "")
    universe = load_universe(root)
    rows = {str(x.get("symbol")): x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"}
    paths_obj = load_json(root / "data/state/intraday_path_features.json", {})
    paths = {str(x.get("symbol")): x for x in (paths_obj.get("features") or []) if x.get("symbol")}

    items = []
    for code, name in universe.items():
        row = rows.get(code)
        if not row:
            continue
        hist_rows = historical_rows(root, code, market_date)
        price = safe_float(row.get("close"))
        history = history_context(hist_rows, price)
        intraday = intraday_context(row, paths.get(code) or {})
        turnover = turnover_context(hist_rows, row, intraday)
        items.append({
            "code": code,
            "name": name,
            "display_name": f"{name}（{code}）",
            "as_of_beijing": row.get("as_of_beijing"),
            "price": row.get("close"),
            "current_return_pct": round4(row.get("change_pct")),
            "historical_context": history,
            "intraday_context": intraday,
            "turnover_acceptance_context": turnover,
            "combined_interpretation_cn": combined_interpretation(history, intraday, turnover, row.get("change_pct")),
            "selection_role_of_current_return": "VALIDATION_ONLY_NOT_CANDIDATE_GENERATOR",
        })

    return {
        "schema_version": "1.1",
        "generated_at": now_utc(),
        "market_date": market_date,
        "as_of_beijing": snapshot.get("captured_at_beijing"),
        "source_snapshot": snapshot_path,
        "mode": "HISTORICAL_TREND_PLUS_INTRADAY_SEQUENCE_PLUS_TURNOVER_CONTEXT",
        "status": "READY" if items else "MISSING",
        "read_only": True,
        "candidate_selection_contract": {
            "rule": "当日涨幅、横截面排名和相对指数强弱只能验证候选，禁止单独生成唯一主候选。",
            "minimum_evidence_classes_for_main_candidate": 2,
            "evidence_classes": [
                "独立假设或低相关收益来源",
                "历史位置与趋势结构支持",
                "日内路径/极值时序/成交承接出现可验证改善",
                "风险收益或下一单位资本效率相对其他候选明显占优"
            ],
            "history_rule": "必须同时解释历史高/中/低位置与趋势状态；位置不等于趋势。历史不可用时明确降级，不得猜测。",
            "intraday_rule": "必须结合高低点出现先后、低点后修复、高点后回撤和采样覆盖区分高位震荡、V型修复候选、冲高回落、低位弱势；MEDIUM或更低采样不得把候选形态表述为已确认。",
            "turnover_rule": "成交承接优先使用按交易时间进度归一化的当前成交额相对近20日平均全天成交额，不以累计成交额绝对值直接判定放量。",
            "evidence_reading_order": [
                "假设/相关性",
                "历史价格位置与趋势",
                "日内路径与极值时序",
                "时间归一化成交承接",
                "风险收益/资本效率",
                "相对强弱",
                "当日涨幅与横截面排名"
            ],
            "cross_section_rule": "正式决策表达优先使用相对指数/ETF池中位数的差值解释强弱；横截面名次仅供后台研究，不得作为主候选理由。",
            "forbidden_shortcut": "先按涨幅或横截面名次筛选候选、再补充理由",
            "no_composite_score": "禁止把历史位置、趋势、日内形态、成交承接和横截面重新合成为机械总分。"
        },
        "items": items,
        "decision_boundary": "本文件只提供历史位置与趋势、日内路径时序及时间归一化成交承接的客观解释，不自动生成风险许可、Trial/Confirm、金额或买卖动作。"
    }


if __name__ == "__main__":
    print(json.dumps(build(ROOT), ensure_ascii=False))
