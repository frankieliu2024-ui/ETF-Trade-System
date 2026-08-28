from __future__ import annotations

import json
from pathlib import Path

try:
    from build_market_regime_context_legacy import ROOT, build as _legacy_build, load_json
    from minute_context_production import build as _minute_build
except ModuleNotFoundError:
    from scripts.build_market_regime_context_legacy import ROOT, build as _legacy_build, load_json
    from scripts.minute_context_production import build as _minute_build


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _range_position(price, low, high):
    p, lo, hi = _f(price), _f(low), _f(high)
    if p is None or lo is None or hi is None or hi == lo:
        return None
    return round((p - lo) / (hi - lo), 4)


def _apply_minute_path(item: dict, feature: dict) -> None:
    item.update({
        "day_range_position": _range_position(item.get("price"), feature.get("path_low"), feature.get("path_high")),
        "path_change_pct": _f(feature.get("path_change_pct")),
        "recent_slope_pct_per_10m": _f(feature.get("recent_slope_pct_per_10m")),
        "recovery_from_path_low_pct": _f(feature.get("recovery_from_path_low_pct")),
        "retreat_from_path_high_pct": _f(feature.get("retreat_from_path_high_pct")),
        "sampling_coverage": "HIGH",
        "path_low": feature.get("path_low"),
        "path_low_as_of_beijing": feature.get("path_low_as_of_beijing"),
        "path_high": feature.get("path_high"),
        "path_high_as_of_beijing": feature.get("path_high_as_of_beijing"),
        "minute_path_source": "TENCENT_1M",
        "minute_latest_as_of_beijing": feature.get("latest_as_of_beijing"),
        "minute_sample_count": feature.get("sample_count"),
        "minute_point_in_time": feature.get("point_in_time"),
        "minute_quote_alignment": feature.get("quote_alignment"),
        "minute_selection_reason": feature.get("production_selection_reason"),
    })


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    base = _legacy_build(root)
    try:
        minute = _minute_build(root)
    except Exception as exc:
        base["minute_path_integration"] = {
            "status": "FALLBACK",
            "selected_count": 0,
            "target_count": 3,
            "error": str(exc)[-500:],
            "formal_latest_price_source_unchanged": True,
        }
        return base

    current = load_json(root / "data/state/CURRENT.json")
    snapshot_path = root / str(current.get("latest_snapshot") or "")
    snapshot = load_json(snapshot_path) if snapshot_path.exists() else {}
    rows = {str(x.get("symbol")): x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"}
    item_map = {str(x.get("code")): x for x in (base.get("indices") or [])}
    names = {"000001": "上证指数", "399006": "创业板指", "000688": "科创50指数"}
    selected, fallback = [], []
    for feature in minute.get("index_items") or []:
        code = str(feature.get("symbol") or "")
        if code not in names:
            continue
        row = rows.get(code) or {}
        item = item_map.get(code)
        if item is None and row:
            item = {
                "display_name": f"{names[code]}（{code}）",
                "code": code,
                "as_of_beijing": row.get("as_of_beijing"),
                "price": row.get("close"),
                "change_pct": _f(row.get("change_pct")),
            }
            base.setdefault("indices", []).append(item)
            item_map[code] = item
        if item is None:
            fallback.append(code)
            continue
        if feature.get("production_usable") is True:
            _apply_minute_path(item, feature)
            selected.append(code)
        else:
            item["minute_path_source"] = "DISCRETE_SNAPSHOT_PATH_FALLBACK"
            item["minute_selection_reason"] = feature.get("production_selection_reason")
            fallback.append(code)

    base["schema_version"] = "1.1"
    base["mode"] = "MARKET_LEVEL_REGIME_CONTEXT_WITH_TENCENT_1M_INDEX_PATH"
    base["minute_path_integration"] = {
        "status": "READY" if len(selected) == 3 else "DEGRADED_WITH_OBJECT_FALLBACK",
        "target_count": 3,
        "selected_count": len(selected),
        "selected_codes": selected,
        "fallback_codes": fallback,
        "source": "tencent_qq minute/query",
        "formal_latest_price_source_unchanged": True,
        "regime_classification_logic_unchanged": True,
        "decision_boundary": minute.get("decision_boundary"),
    }
    required = (base.get("analysis_contract") or {}).get("required_dimensions") or []
    k50 = "科创50指数（000688）日内位置与路径"
    if k50 not in required:
        required.insert(2, k50)
    return base


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
