from __future__ import annotations

import json
import os
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


def history_context(rows: list[dict], current_price: float | None) -> dict:
    if current_price is None or not rows:
        return {
            "status": "HISTORY_UNAVAILABLE",
            "sample_count": len(rows),
            "historical_zone": "UNKNOWN",
            "interpretation": "缺少足够历史日线，不判断当前处于历史高位、低位或中位。",
        }
    closes = [safe_float(x.get("close")) for x in rows]
    closes = [x for x in closes if x is not None]
    if not closes:
        return {"status": "HISTORY_UNAVAILABLE", "sample_count": 0, "historical_zone": "UNKNOWN"}

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
        "return_vs_5_sessions_ago_pct": lag_return(5),
        "return_vs_20_sessions_ago_pct": lag_return(20),
        "window_20": w20,
        "window_60": w60,
        "historical_zone": zone,
        "interpretation": f"按可用历史日线，当前处于{zone_cn}；当日涨幅必须结合该位置解释，不能把高位延续与低位反弹视为同一种强势。",
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

    if "V_RECOVERY_CANDIDATE" in candidates or ((recovery or 0) >= 0.5 and (day_pos or 0) >= 0.65 and (path_change or 0) >= 0):
        morphology = "V_OR_LOW_POINT_RECOVERY"
        cn = "更接近低点修复/V型反转候选"
    elif day_pos is not None and day_pos >= 0.75 and (slope is None or slope >= -0.03):
        morphology = "HIGH_ZONE_HOLD"
        cn = "更接近当日高位震荡/强势维持"
    elif (retreat is not None and retreat <= -0.5) or (day_pos is not None and day_pos <= 0.35 and (slope or 0) < 0):
        morphology = "FADE_FROM_HIGH"
        cn = "更接近冲高回落/高位回撤"
    elif day_pos is not None and day_pos <= 0.25:
        morphology = "LOW_ZONE_WEAK"
        cn = "更接近当日低位弱势"
    else:
        morphology = "MID_RANGE_OR_UNRESOLVED"
        cn = "处于当日区间中部或结构尚未确认"

    return {
        "morphology": morphology,
        "interpretation_cn": cn,
        "day_range_position": round4(day_pos),
        "open_return_pct": round4(pct(row.get("open"), row.get("prev_close"))),
        "current_return_pct": round4(row.get("change_pct")),
        "path_change_pct": round4(path_change),
        "recovery_from_path_low_pct": round4(recovery),
        "retreat_from_path_high_pct": round4(retreat),
        "recent_slope_pct_per_10m": round4(slope),
        "sampling_coverage": (path.get("sampling") or {}).get("coverage"),
        "structure_candidates": candidates,
        "sampling_limitation": (path.get("sampling") or {}).get("limitation"),
    }


def combined_interpretation(history: dict, intraday: dict, current_return) -> str:
    ret = safe_float(current_return)
    zone = history.get("historical_zone")
    morphology = intraday.get("morphology")
    if history.get("status") != "READY":
        history_phrase = "历史位置证据不足"
    elif zone == "HISTORICAL_HIGH_ZONE" and (ret or 0) > 0:
        history_phrase = "上涨发生在历史高位区，更偏高位延续/突破，需要验证承接而非按低位反弹解释"
    elif zone == "HISTORICAL_LOW_ZONE" and (ret or 0) > 0:
        history_phrase = "上涨发生在历史低位区，更偏超跌/低位反弹，需要验证是否从修复升级为趋势"
    elif zone == "HISTORICAL_HIGH_ZONE" and (ret or 0) < 0:
        history_phrase = "回落发生在历史高位区，需要区分健康整理与趋势破坏"
    elif zone == "HISTORICAL_LOW_ZONE" and (ret or 0) < 0:
        history_phrase = "下跌发生在历史低位区，赔率可能改善但承接仍弱"
    else:
        history_phrase = "当前位于历史中位区，单日涨跌不能单独定义机会性质"
    intraday_phrase = intraday.get("interpretation_cn") or "日内结构未确认"
    return f"{history_phrase}；日内{intraday_phrase}。"


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
        price = safe_float(row.get("close"))
        history = history_context(historical_rows(root, code, market_date), price)
        intraday = intraday_context(row, paths.get(code) or {})
        items.append({
            "code": code,
            "name": name,
            "display_name": f"{name}（{code}）",
            "as_of_beijing": row.get("as_of_beijing"),
            "price": row.get("close"),
            "current_return_pct": round4(row.get("change_pct")),
            "historical_context": history,
            "intraday_context": intraday,
            "combined_interpretation_cn": combined_interpretation(history, intraday, row.get("change_pct")),
            "selection_role_of_current_return": "VALIDATION_ONLY_NOT_CANDIDATE_GENERATOR",
        })

    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "market_date": market_date,
        "as_of_beijing": snapshot.get("captured_at_beijing"),
        "source_snapshot": snapshot_path,
        "mode": "HISTORICAL_PLUS_INTRADAY_STRUCTURE_CONTEXT",
        "status": "READY" if items else "MISSING",
        "read_only": True,
        "candidate_selection_contract": {
            "rule": "当日涨幅、横截面排名和相对指数强弱只能验证候选，禁止单独生成唯一主候选。",
            "minimum_evidence_classes_for_main_candidate": 2,
            "evidence_classes": [
                "独立假设或低相关收益来源",
                "价格结构/日内承接出现可验证改善",
                "风险收益或下一单位资本效率相对其他候选明显占优"
            ],
            "history_rule": "必须先解释当前价格处于历史高位、中位还是低位；历史不可用时明确降级，不得猜测。",
            "intraday_rule": "必须区分当日高位震荡、低点修复/V型反转候选、冲高回落和低位弱势；离散采样不足时明确限制。",
            "evidence_reading_order": [
                "假设/相关性",
                "历史价格位置",
                "日内路径与承接",
                "风险收益/资本效率",
                "相对强弱",
                "当日涨幅与横截面排名"
            ],
            "forbidden_shortcut": "先按涨幅排名筛选候选、再补充理由"
        },
        "items": items,
        "decision_boundary": "本文件只提供价格所处历史位置和当日日内路径的客观解释，不自动生成风险许可、Trial/Confirm、金额或买卖动作。"
    }


if __name__ == "__main__":
    print(json.dumps(build(ROOT), ensure_ascii=False))
