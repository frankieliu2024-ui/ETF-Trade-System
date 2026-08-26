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
DAILY_DIR = Path("events/research/daily_features")
DECISION_DIR = Path("events/decisions")
OUTCOME_DIR = Path("events/research/decision_outcomes")


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


def parse_dt(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except Exception:
        return None


def read_latest_snapshot(root: Path, current: dict) -> tuple[str, dict]:
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


def load_universe(root: Path) -> dict[str, str]:
    cfg = load_json(root / "config/market/etf_monitor_universe.json", {})
    return {str(x.get("code")): str(x.get("name")) for x in (cfg.get("objects") or []) if x.get("code")}


def path_feature_map(root: Path) -> dict[str, dict]:
    obj = load_json(root / "data/state/intraday_path_features.json", {})
    return {str(x.get("symbol")): x for x in (obj.get("features") or []) if x.get("symbol")}


def build_daily_features(root: Path, current: dict, snapshot_path: str, snapshot: dict) -> dict:
    market_date = str(snapshot.get("market_date") or current.get("market_date") or "")
    universe = load_universe(root)
    path_map = path_feature_map(root)
    rows = {str(x.get("symbol")): x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"}
    etf_rows = [rows[c] for c in universe if c in rows]
    universe_changes = [safe_float(x.get("change_pct")) for x in etf_rows]
    universe_changes = [x for x in universe_changes if x is not None]
    universe_median = median(universe_changes) if universe_changes else None
    index_rows = {c: rows.get(c) for c in ("000001", "399006", "000688")}

    features = []
    for code, name in universe.items():
        row = rows.get(code)
        if not row:
            continue
        pf = path_map.get(code) or {}
        change = safe_float(row.get("change_pct"))
        rel = {}
        for bench_code, bench in index_rows.items():
            bench_change = safe_float((bench or {}).get("change_pct"))
            if change is not None and bench_change is not None:
                rel[bench_code] = round4(change - bench_change)
        features.append({
            "code": code,
            "name": name,
            "as_of_beijing": row.get("as_of_beijing"),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "prev_close": row.get("prev_close"),
            "volume": row.get("volume"),
            "amount": row.get("amount"),
            "open_return_pct": round4(pct(row.get("open"), row.get("prev_close"))),
            "close_return_pct": round4(change),
            "day_high_return_pct": round4(pct(row.get("high"), row.get("prev_close"))),
            "day_low_return_pct": round4(pct(row.get("low"), row.get("prev_close"))),
            "close_range_position": round4((safe_float(row.get("close")) - safe_float(row.get("low"))) / (safe_float(row.get("high")) - safe_float(row.get("low"))) if all(safe_float(row.get(k)) is not None for k in ("close", "low", "high")) and safe_float(row.get("high")) != safe_float(row.get("low")) else None),
            "relative_to_indices_pct_points": rel,
            "relative_to_etf_universe_median_pct_points": round4(change - universe_median) if change is not None and universe_median is not None else None,
            "intraday_path": {
                "sample_count": pf.get("sample_count"),
                "path_change_pct": pf.get("path_change_pct"),
                "recovery_from_path_low_pct": pf.get("recovery_from_path_low_pct"),
                "retreat_from_path_high_pct": pf.get("retreat_from_path_high_pct"),
                "recent_slope_pct_per_10m": pf.get("recent_slope_pct_per_10m"),
                "sampling_coverage": (pf.get("sampling") or {}).get("coverage"),
                "structure_candidates": [x.get("label") for x in (pf.get("structure_candidates") or []) if x.get("label")],
            },
            "provider": row.get("provider"),
            "quality_status": row.get("quality_status"),
        })

    ranked = sorted([x for x in features if x.get("close_return_pct") is not None], key=lambda x: x["close_return_pct"], reverse=True)
    rank_map = {x["code"]: i + 1 for i, x in enumerate(ranked)}
    for item in features:
        item["etf_universe_close_return_rank"] = rank_map.get(item["code"])

    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "market_date": market_date,
        "as_of_beijing": snapshot.get("captured_at_beijing"),
        "market_phase": snapshot.get("market_phase"),
        "source_snapshot": snapshot_path,
        "quality_status": snapshot.get("quality_status"),
        "mode": "OBJECTIVE_RESEARCH_FEATURES",
        "read_only": True,
        "decision_boundary": "研究特征只提供事实和统计证据，不生成风险许可、机会状态、生命周期、金额或买卖动作。",
        "etf_universe_count": len(universe),
        "observed_etf_count": len(features),
        "etf_universe_median_close_return_pct": round4(universe_median),
        "features": features,
    }


def build_relative_strength(daily: dict) -> dict:
    items = []
    for x in daily.get("features") or []:
        items.append({
            "code": x.get("code"),
            "name": x.get("name"),
            "close_return_pct": x.get("close_return_pct"),
            "rank": x.get("etf_universe_close_return_rank"),
            "vs_universe_median_pct_points": x.get("relative_to_etf_universe_median_pct_points"),
            "vs_shanghai_pct_points": (x.get("relative_to_indices_pct_points") or {}).get("000001"),
            "vs_chinext_pct_points": (x.get("relative_to_indices_pct_points") or {}).get("399006"),
            "vs_star50_pct_points": (x.get("relative_to_indices_pct_points") or {}).get("000688"),
            "recent_slope_pct_per_10m": (x.get("intraday_path") or {}).get("recent_slope_pct_per_10m"),
            "sampling_coverage": (x.get("intraday_path") or {}).get("sampling_coverage"),
        })
    items.sort(key=lambda x: (x.get("rank") is None, x.get("rank") or 9999))
    return {
        "generated_at": now_utc(),
        "market_date": daily.get("market_date"),
        "as_of_beijing": daily.get("as_of_beijing"),
        "mode": "OBJECTIVE_RELATIVE_STRENGTH",
        "read_only": True,
        "decision_boundary": "排名用于统一比较和研究优先级，不代表自动买强卖弱或自动轮动。",
        "items": items,
    }


def update_provider_metrics(root: Path, snapshot_path: str, snapshot: dict) -> dict:
    path = root / "data/state/provider_metrics.json"
    state = load_json(path, {"schema_version": "1.0", "providers": {}, "processed_observations": []})
    processed = set(state.get("processed_observations") or [])
    providers = state.setdefault("providers", {})

    def observe(key: str, provider: str, quality: str, lag_seconds=None, source_type=""):
        if not provider or key in processed:
            return
        processed.add(key)
        p = providers.setdefault(provider, {
            "observation_count": 0,
            "pass_count": 0,
            "degraded_or_fail_count": 0,
            "lag_sample_count": 0,
            "lag_seconds_sum": 0.0,
            "lag_seconds_max": 0.0,
            "source_types": [],
        })
        p["observation_count"] += 1
        if str(quality).upper() == "PASS":
            p["pass_count"] += 1
        else:
            p["degraded_or_fail_count"] += 1
        if lag_seconds is not None and lag_seconds >= 0:
            p["lag_sample_count"] += 1
            p["lag_seconds_sum"] += float(lag_seconds)
            p["lag_seconds_max"] = max(float(p["lag_seconds_max"]), float(lag_seconds))
        if source_type and source_type not in p["source_types"]:
            p["source_types"].append(source_type)

    captured = parse_dt(snapshot.get("captured_at_beijing"))
    for row in snapshot.get("rows") or []:
        provider = str(row.get("provider") or snapshot.get("provider") or "")
        as_of = parse_dt(row.get("as_of_beijing"))
        lag = (captured - as_of).total_seconds() if captured and as_of else None
        key = f"snapshot|{snapshot_path}|{row.get('symbol')}|{row.get('as_of_beijing')}"
        observe(key, provider, row.get("quality_status") or snapshot.get("quality_status"), lag, "A_SHARE_SNAPSHOT")

    overseas = load_json(root / "data/state/overseas_context.json", {})
    generated = parse_dt(overseas.get("generated_at_beijing"))
    for obj_name, obj in (overseas.get("objects") or {}).items():
        latest = obj.get("latest") or {}
        as_of = parse_dt(latest.get("as_of_beijing"))
        lag = (generated - as_of).total_seconds() if generated and as_of else None
        key = f"overseas|{obj_name}|{obj.get('provider')}|{latest.get('as_of_beijing')}"
        observe(key, str(obj.get("provider") or ""), obj.get("quality_status"), lag, "OVERSEAS_INDEX")

    for provider, p in providers.items():
        n = int(p.get("observation_count") or 0)
        lag_n = int(p.get("lag_sample_count") or 0)
        p["pass_rate"] = round4((p.get("pass_count", 0) / n * 100.0) if n else None)
        p["mean_lag_seconds"] = round4((p.get("lag_seconds_sum", 0.0) / lag_n) if lag_n else None)
        p["source_types"] = sorted(p.get("source_types") or [])

    state.update({
        "generated_at": now_utc(),
        "mode": "PROVIDER_OBSERVABILITY_METRICS",
        "read_only": True,
        "decision_boundary": "provider指标用于数据质量与维护，不产生交易动作，也不自动修改provider优先级。",
        "processed_observations": sorted(processed),
        "providers": providers,
    })
    atomic_json_write(path, state)
    return state


def daily_history(root: Path) -> list[dict]:
    base = root / DAILY_DIR
    if not base.exists():
        return []
    out = []
    for p in sorted(base.glob("*.json")):
        obj = load_json(p, {})
        if obj.get("market_date"):
            out.append(obj)
    return out


def update_decision_outcomes(root: Path) -> int:
    decisions = root / DECISION_DIR
    if not decisions.exists():
        return 0
    history = daily_history(root)
    by_date = {x.get("market_date"): x for x in history}
    dates = sorted(by_date)
    count = 0
    for event_path in sorted(decisions.glob("*.json")):
        event = load_json(event_path, {})
        code = str(event.get("candidate_code") or "")
        decision_date = str(event.get("market_date") or "")
        price0 = safe_float(event.get("price_at_decision"))
        if not code or not decision_date or price0 in (None, 0.0):
            continue
        later_dates = [d for d in dates if d >= decision_date]
        results = {}
        mfe, mae = None, None
        for idx, date in enumerate(later_dates):
            daily = by_date[date]
            row = next((x for x in (daily.get("features") or []) if str(x.get("code")) == code), None)
            if not row:
                continue
            close_ret = pct(row.get("close"), price0)
            if idx == 0:
                results["decision_day_close_return_pct"] = round4(close_ret)
                continue
            if idx in (1, 3, 5):
                results[f"T_plus_{idx}_close_return_pct"] = round4(close_ret)
            hi_ret, lo_ret = pct(row.get("high"), price0), pct(row.get("low"), price0)
            if hi_ret is not None:
                mfe = hi_ret if mfe is None else max(mfe, hi_ret)
            if lo_ret is not None:
                mae = lo_ret if mae is None else min(mae, lo_ret)
            if idx >= 5:
                break
        outcome = {
            "schema_version": "1.0",
            "generated_at": now_utc(),
            "decision_id": event.get("decision_id") or event_path.stem,
            "market_date": decision_date,
            "candidate_code": code,
            "candidate_name": event.get("candidate_name"),
            "price_at_decision": price0,
            "formal_decision": event.get("formal_decision"),
            "results": results,
            "MFE_through_T_plus_5_pct_excluding_decision_day": round4(mfe),
            "MAE_through_T_plus_5_pct_excluding_decision_day": round4(mae),
            "method_note": "T+N按后续已记录A股交易日顺序计算；为避免使用决策前的当日高低点，MFE/MAE排除决策当日。",
            "read_only": True,
            "decision_boundary": "结果归因仅用于复盘与研究，不自动修改MASTER或产生新交易动作。",
        }
        out_path = root / OUTCOME_DIR / f"{outcome['decision_id']}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(out_path, outcome)
        count += 1
    return count


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    snapshot_path, snapshot = read_latest_snapshot(root, current)
    if not snapshot:
        return {"status": "NO_SNAPSHOT", "generated_at": now_utc(), "decision_output_generated": False}
    daily = build_daily_features(root, current, snapshot_path, snapshot)
    daily_path = root / DAILY_DIR / f"{daily.get('market_date')}.json"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(daily_path, daily)
    relative = build_relative_strength(daily)
    atomic_json_write(root / "data/state/relative_strength.json", relative)
    provider = update_provider_metrics(root, snapshot_path, snapshot)
    outcome_count = update_decision_outcomes(root)
    return {
        "status": "READY",
        "generated_at": now_utc(),
        "market_date": daily.get("market_date"),
        "daily_feature_count": len(daily.get("features") or []),
        "relative_strength_count": len(relative.get("items") or []),
        "provider_count": len(provider.get("providers") or {}),
        "decision_outcome_count": outcome_count,
        "decision_output_generated": False,
    }


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data/state/research_context.json", {
        **result,
        "read_only": True,
        "decision_boundary": "研究层只提供事实、相对强弱、数据质量和决策结果归因；不得绕过MASTER生成交易动作。",
        "paths": {
            "daily_features": "events/research/daily_features/<market_date>.json",
            "relative_strength": "data/state/relative_strength.json",
            "provider_metrics": "data/state/provider_metrics.json",
            "decision_events": "events/decisions/<decision_id>.json",
            "decision_outcomes": "events/research/decision_outcomes/<decision_id>.json",
        },
    })
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
