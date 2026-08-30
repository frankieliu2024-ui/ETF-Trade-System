from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from statistics import median

try:
    from state_manager import atomic_json_write, now_utc, read_current
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_current

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()

# These constants only classify observed path geometry. They are NOT trading-rule thresholds
# and must never generate risk permission, opportunity state, amount, or buy/sell actions.
FAST_MOVE_PCT = 1.0
FAST_MOVE_MAX_MINUTES = 20.0
V_RECOVERY_PCT = 1.0
HIGH_ZONE_POSITION = 0.75
HIGH_ZONE_SPREAD_PCT = 1.0
LATE_SESSION_REFERENCE_MINUTE = 14 * 60 + 30


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_time(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except Exception:
        return None


def safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0.0):
        return None
    return (new / old - 1.0) * 100.0


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def snapshot_series(root: Path, market_date: str) -> list[tuple[str, dict]]:
    snapshot_dir = root / "data" / "market" / "snapshots"
    items: list[tuple[str, dict]] = []
    if not snapshot_dir.exists() or not market_date:
        return items
    for path in sorted(snapshot_dir.glob(f"{market_date}_*.json")):
        try:
            obj = load_json(path)
        except Exception:
            continue
        # A DEGRADED snapshot still contains valid PASS rows for most symbols. Keep the
        # snapshot in the path series, then filter unusable rows in build_points().
        if obj.get("market_date") != market_date or obj.get("quality_status") not in {"PASS", "DEGRADED"}:
            continue
        phase = str(obj.get("market_phase") or "")
        # Keep opening auction separately out of continuous-path geometry.
        if phase == "OPENING_CALL_AUCTION":
            continue
        captured = str(obj.get("captured_at_beijing") or obj.get("captured_at") or "")
        if not captured:
            continue
        items.append((str(path.relative_to(root)).replace("\\", "/"), obj))
    items.sort(key=lambda x: str(x[1].get("captured_at_beijing") or x[1].get("captured_at") or ""))
    return items


def build_points(items: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    by_symbol: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}
    for snapshot_path, snap in items:
        captured = str(snap.get("captured_at_beijing") or snap.get("captured_at") or "")
        for row in snap.get("rows") or []:
            symbol = str(row.get("symbol") or "")
            price = safe_float(row.get("close"))
            if not symbol or price is None or row.get("quality_status") != "PASS":
                continue
            as_of = str(row.get("as_of_beijing") or captured)
            key = f"{as_of}|{price}"
            seen.setdefault(symbol, set())
            if key in seen[symbol]:
                continue
            seen[symbol].add(key)
            by_symbol.setdefault(symbol, []).append({
                "as_of_beijing": as_of,
                "price": price,
                "day_high": safe_float(row.get("high")),
                "day_low": safe_float(row.get("low")),
                "change_pct": safe_float(row.get("change_pct")),
                "volume": safe_float(row.get("volume")),
                "amount": safe_float(row.get("amount")),
                "asset_class": row.get("asset_class", ""),
                "snapshot": snapshot_path,
            })
    for points in by_symbol.values():
        points.sort(key=lambda p: p["as_of_beijing"])
    return by_symbol


def interval_minutes(a: dict, b: dict) -> float | None:
    t0, t1 = parse_time(a.get("as_of_beijing", "")), parse_time(b.get("as_of_beijing", ""))
    if t0 is None or t1 is None:
        return None
    return max(0.0, (t1 - t0).total_seconds() / 60.0)


def path_position(price: float, low: float, high: float) -> float | None:
    if high <= low:
        return None
    return (price - low) / (high - low)


def late_session_context(points: list[dict]) -> dict:
    latest = points[-1]
    latest_dt = parse_time(latest.get("as_of_beijing", ""))
    if latest_dt is None or latest_dt.hour * 60 + latest_dt.minute < LATE_SESSION_REFERENCE_MINUTE:
        return {
            "status": "NOT_YET_AVAILABLE",
            "reference_clock_beijing": "14:30",
            "move_from_reference_pct": None,
            "decision_boundary": "14:30前不构造尾盘方向；不得用未来节点或旧交易日替代。",
        }
    eligible = []
    for point in points:
        dt = parse_time(point.get("as_of_beijing", ""))
        if dt is not None and dt.hour * 60 + dt.minute >= LATE_SESSION_REFERENCE_MINUTE:
            eligible.append((dt, point))
    if not eligible:
        return {
            "status": "DEGRADED",
            "reference_clock_beijing": "14:30",
            "move_from_reference_pct": None,
            "reason": "no point at or after 14:30",
        }
    _, reference = eligible[0]
    return {
        "status": "READY",
        "reference_clock_beijing": "14:30",
        "reference_as_of_beijing": reference.get("as_of_beijing"),
        "reference_price": reference.get("price"),
        "latest_as_of_beijing": latest.get("as_of_beijing"),
        "latest_price": latest.get("price"),
        "move_from_reference_pct": round_or_none(pct_change(latest.get("price"), reference.get("price"))),
        "reference_sampling_role": "FIRST_VALID_OBSERVATION_AT_OR_AFTER_14_30",
        "decision_boundary": "离散快照只描述14:30后已观察到的价格变化；采样间隔不足以证明未采样分钟内的完整形态。",
    }


def classify(points: list[dict], low_idx: int, high_idx: int, path_low: float, path_high: float) -> list[dict]:
    labels: list[dict] = []
    latest = points[-1]
    latest_price = latest["price"]
    intervals: list[dict] = []
    for left, right in zip(points, points[1:]):
        mins = interval_minutes(left, right)
        move = pct_change(right["price"], left["price"])
        if mins is None or move is None:
            continue
        intervals.append({"from": left["as_of_beijing"], "to": right["as_of_beijing"], "minutes": mins, "move_pct": move})
    fast_up = [x for x in intervals if x["minutes"] <= FAST_MOVE_MAX_MINUTES and x["move_pct"] >= FAST_MOVE_PCT]
    fast_down = [x for x in intervals if x["minutes"] <= FAST_MOVE_MAX_MINUTES and x["move_pct"] <= -FAST_MOVE_PCT]
    if fast_up:
        best = max(fast_up, key=lambda x: x["move_pct"])
        labels.append({"label": "RAPID_RISE_CANDIDATE", "evidence": best})
    if fast_down:
        worst = min(fast_down, key=lambda x: x["move_pct"])
        labels.append({"label": "SHARP_DROP_CANDIDATE", "evidence": worst})

    recovery = pct_change(latest_price, path_low)
    latest_position = path_position(latest_price, path_low, path_high)
    points_after_low = len(points) - low_idx - 1
    if low_idx < len(points) - 1 and recovery is not None and recovery >= V_RECOVERY_PCT and points_after_low >= 2 and (latest_position is None or latest_position >= 0.5):
        labels.append({
            "label": "V_RECOVERY_CANDIDATE",
            "evidence": {
                "low_as_of_beijing": points[low_idx]["as_of_beijing"],
                "recovery_from_path_low_pct": round_or_none(recovery),
                "points_after_low": points_after_low,
                "latest_range_position": round_or_none(latest_position),
            },
        })

    tail = points[-3:]
    if len(tail) >= 3 and path_high > path_low:
        positions = [path_position(p["price"], path_low, path_high) for p in tail]
        tail_prices = [p["price"] for p in tail]
        spread = pct_change(max(tail_prices), min(tail_prices))
        if all(p is not None and p >= HIGH_ZONE_POSITION for p in positions) and spread is not None and spread <= HIGH_ZONE_SPREAD_PCT:
            labels.append({
                "label": "HIGH_ZONE_CONSOLIDATION_CANDIDATE",
                "evidence": {"tail_points": len(tail), "tail_spread_pct": round_or_none(spread), "range_positions": [round_or_none(p) for p in positions]},
            })
    return labels


def feature_for_symbol(symbol: str, points: list[dict], benchmarks: dict[str, list[dict]]) -> dict:
    prices = [p["price"] for p in points]
    path_low, path_high = min(prices), max(prices)
    low_idx, high_idx = prices.index(path_low), prices.index(path_high)
    first, latest = points[0], points[-1]
    gaps = [interval_minutes(a, b) for a, b in zip(points, points[1:])]
    valid_gaps = [x for x in gaps if x is not None]
    recent_move = pct_change(latest["price"], points[-2]["price"]) if len(points) >= 2 else None
    recent_minutes = interval_minutes(points[-2], latest) if len(points) >= 2 else None
    recent_slope_10m = None
    if recent_move is not None and recent_minutes not in (None, 0.0):
        recent_slope_10m = recent_move / recent_minutes * 10.0
    amount_delta = None
    volume_delta = None
    if len(points) >= 2:
        if latest.get("amount") is not None and points[-2].get("amount") is not None:
            amount_delta = latest["amount"] - points[-2]["amount"]
        if latest.get("volume") is not None and points[-2].get("volume") is not None:
            volume_delta = latest["volume"] - points[-2]["volume"]

    day_high = latest.get("day_high") if latest.get("day_high") is not None else path_high
    day_low = latest.get("day_low") if latest.get("day_low") is not None else path_low
    latest_day_position = path_position(latest["price"], day_low, day_high) if day_high is not None and day_low is not None else None

    rel = {}
    for bench_symbol in ("000001", "399006"):
        bench_points = benchmarks.get(bench_symbol) or []
        if not bench_points:
            continue
        bench_latest = bench_points[-1]
        own_chg = latest.get("change_pct")
        bench_chg = bench_latest.get("change_pct")
        if own_chg is not None and bench_chg is not None:
            rel[bench_symbol] = {
                "latest_relative_change_pct_points": round_or_none(own_chg - bench_chg),
                "benchmark_change_pct": round_or_none(bench_chg),
                "benchmark_as_of_beijing": bench_latest.get("as_of_beijing"),
            }

    max_gap = max(valid_gaps) if valid_gaps else None
    med_gap = median(valid_gaps) if valid_gaps else None
    coverage = "HIGH" if len(points) >= 8 and (max_gap is None or max_gap <= 20) else ("MEDIUM" if len(points) >= 4 else "LOW")
    if max_gap is not None and max_gap > 30:
        coverage = "LOW" if len(points) < 8 else "MEDIUM"

    return {
        "symbol": symbol,
        "asset_class": latest.get("asset_class", ""),
        "sample_count": len(points),
        "first_as_of_beijing": first["as_of_beijing"],
        "latest_as_of_beijing": latest["as_of_beijing"],
        "first_price": first["price"],
        "latest_price": latest["price"],
        "path_change_pct": round_or_none(pct_change(latest["price"], first["price"])),
        "path_low": path_low,
        "path_low_as_of_beijing": points[low_idx]["as_of_beijing"],
        "path_high": path_high,
        "path_high_as_of_beijing": points[high_idx]["as_of_beijing"],
        "recovery_from_path_low_pct": round_or_none(pct_change(latest["price"], path_low)),
        "retreat_from_path_high_pct": round_or_none(pct_change(latest["price"], path_high)),
        "latest_day_range_position": round_or_none(latest_day_position),
        "late_session_context": late_session_context(points),
        "recent_interval_minutes": round_or_none(recent_minutes, 2),
        "recent_move_pct": round_or_none(recent_move),
        "recent_slope_pct_per_10m": round_or_none(recent_slope_10m),
        "recent_volume_delta": round_or_none(volume_delta, 2),
        "recent_amount_delta": round_or_none(amount_delta, 2),
        "relative_to_indices": rel,
        "sampling": {
            "coverage": coverage,
            "median_gap_minutes": round_or_none(med_gap, 2),
            "max_gap_minutes": round_or_none(max_gap, 2),
            "limitation": "离散脉冲只能重建近实时路径；两次采样之间的瞬时拉升/急跌可能遗漏。日内high/low可证明期间发生过极值，但不能单独确定具体分钟形态。",
        },
        "structure_candidates": classify(points, low_idx, high_idx, path_low, path_high),
    }


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    market_date = str(current.get("market_date") or "")
    items = snapshot_series(root, market_date)
    by_symbol = build_points(items)
    base = {
        "generated_at": now_utc(),
        "market_date": market_date,
        "mode": "OBJECTIVE_INTRADAY_PATH_FEATURES",
        "read_only": True,
        "decision_boundary": "只描述离散行情脉冲形成的日内路径几何、成交增量、14:30后已观察路径和相对强弱；结构候选标签不是交易信号，不生成风险许可、机会状态、金额或买卖动作。",
        "label_boundary": "RAPID_RISE/SHARP_DROP/V_RECOVERY/HIGH_ZONE_CONSOLIDATION均为描述性候选标签。必须结合采样覆盖、实际时点、市场阶段及MASTER完整决策链解释。",
    }
    if not items or not by_symbol:
        return {**base, "status": "INSUFFICIENT_HISTORY", "snapshot_count": len(items), "features": []}
    features = [feature_for_symbol(symbol, points, by_symbol) for symbol, points in sorted(by_symbol.items()) if points]
    return {
        **base,
        "status": "READY" if len(items) >= 2 else "INSUFFICIENT_HISTORY",
        "snapshot_count": len(items),
        "first_snapshot": items[0][0],
        "latest_snapshot": items[-1][0],
        "features": features,
    }


def main() -> None:
    result = build(ROOT)
    target = ROOT / "data" / "state" / "intraday_path_features.json"
    atomic_json_write(target, result)
    print(json.dumps({"ok": True, "status": result.get("status"), "market_date": result.get("market_date"), "snapshot_count": result.get("snapshot_count"), "feature_count": len(result.get("features") or [])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
