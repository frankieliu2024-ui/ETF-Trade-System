from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from build_minute_path_features import fetch_one, quality_requirements
except ModuleNotFoundError:
    from scripts.build_minute_path_features import fetch_one, quality_requirements

BEIJING = ZoneInfo("Asia/Shanghai")
ACTIVE_PHASES = {"CONTINUOUS_MORNING", "CONTINUOUS_AFTERNOON", "CLOSING_CALL_AUCTION", "OPENING_CALL_AUCTION", "OPENING_AUCTION"}


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _provider_time(row: dict):
    value = row.get("provider_timestamp_ms")
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(float(value) / 1000.0, tz=BEIJING)


def _post_close_usable(feature: dict, requirements: dict) -> tuple[bool, str]:
    if feature.get("status") != "READY":
        return False, "minute_fetch_not_ready"
    if not bool((feature.get("point_in_time") or {}).get("pass")):
        return False, "point_in_time_failed"
    if not bool((feature.get("sampling") or {}).get("continuity_pass")):
        return False, "continuity_failed"
    alignment = feature.get("quote_alignment") or {}
    diff = _f(alignment.get("abs_diff_pct"))
    if diff is None or diff > float(requirements.get("quote_alignment_max_abs_diff_pct", 0.25)):
        return False, "terminal_price_alignment_failed"
    return True, "same_day_terminal_path_price_aligned"


def build_stock_minute_feature(root: Path, stock_row: dict, market_date: str, market_phase: str | None = None) -> dict:
    code = str(stock_row.get("code") or stock_row.get("symbol") or "")
    name = str(stock_row.get("name") or code)
    thscode = str(stock_row.get("thscode") or "")
    if not code or not thscode or not market_date:
        return {"status": "MISSING", "production_usable": False, "production_selection_reason": "missing_stock_identity_or_market_date"}
    quote = {
        "last_price": stock_row.get("close", stock_row.get("latest_price")),
        "provider_timestamp_ms": stock_row.get("provider_timestamp_ms"),
    }
    formal_row = {
        "close": stock_row.get("close", stock_row.get("latest_price")),
        "as_of_beijing": stock_row.get("as_of_beijing", stock_row.get("data_time_beijing")),
    }
    requirements = quality_requirements(root)
    try:
        feature = fetch_one(code, name, thscode, market_date, formal_row, quote, requirements)
    except Exception as exc:
        return {
            "symbol": code,
            "name": name,
            "thscode": thscode,
            "asset_class": "A_SHARE_STOCK",
            "status": "FAILED",
            "production_usable": False,
            "production_selection_reason": "minute_fetch_exception",
            "error": str(exc)[-500:],
            "decision_boundary": "个股分钟失败只回退该对象现有quote/日内OHLC，不阻断A股指数、ETF或其他个股。",
        }
    feature["asset_class"] = "A_SHARE_STOCK"
    raw_volume_delta = _f(feature.get("recent_volume_delta"))
    if raw_volume_delta is not None:
        feature["recent_volume_delta_raw_hand"] = raw_volume_delta
        feature["recent_volume_delta"] = round(raw_volume_delta * 100.0, 4)
    feature["volume_unit"] = "share"
    feature["source_volume_unit"] = "hand"
    feature["volume_normalization_factor"] = 100
    feature["amount_unit"] = "CNY"
    phase = str(market_phase or stock_row.get("market_phase") or "")
    if phase in ACTIVE_PHASES:
        usable = bool(feature.get("item_quality_pass"))
        reason = "live_quality_gate_pass" if usable else "live_quality_gate_failed"
    else:
        usable, reason = _post_close_usable(feature, requirements)
    feature["production_usable"] = usable
    feature["production_selection_reason"] = reason
    feature["provider_role"] = "PRIMARY_A_SHARE_STOCK_MINUTE_PATH"
    feature["formal_latest_price_source_unchanged"] = True
    feature["decision_boundary"] = "腾讯1分钟只增强A股个股日内路径、极值时序和最近成交参与；正式最新价仍由quote router/个股quote链决定，不独立产生ETF或个股交易动作。"
    return feature
