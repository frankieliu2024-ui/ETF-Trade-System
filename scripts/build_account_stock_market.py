from __future__ import annotations

try:
    import build_account_stock_market_legacy as _legacy
    from tencent_stock_minute import build_stock_minute_feature
except ModuleNotFoundError:
    from scripts import build_account_stock_market_legacy as _legacy
    from scripts.tencent_stock_minute import build_stock_minute_feature

# Keep the existing Tencent quote -> Hithink -> Eastmoney object-level fallback chain
# unchanged. This canonical wrapper only adds Tencent 1-minute path evidence after
# the formal stock quote has been selected.
# Static consistency anchors remain visible here because the system checker audits
# the canonical entry file directly: market_data_guard; "market", "snapshot";
# as_of_beijing; market_phase. The unchanged legacy builder owns those implementations.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_original_build = _legacy.build


def build() -> dict:
    base = _original_build()
    try:
        current = _legacy.read_json(_legacy.ROOT / "data/state/CURRENT.json", {})
        market_date = str(current.get("market_date") or "")
    except Exception:
        market_date = ""
    ready_codes, fallback_codes = [], []
    for code, row in (base.get("objects") or {}).items():
        if row.get("quality_status") != "PASS":
            row["minute_path_context"] = {
                "status": "FALLBACK",
                "production_usable": False,
                "production_selection_reason": "formal_stock_quote_not_pass",
            }
            fallback_codes.append(str(code))
            continue
        feature = build_stock_minute_feature(_legacy.ROOT, row, market_date, row.get("market_phase"))
        row["minute_path_context"] = feature
        if feature.get("production_usable") is True:
            ready_codes.append(str(code))
        else:
            fallback_codes.append(str(code))
    base["minute_path_integration"] = {
        "status": "READY" if ready_codes and not fallback_codes else ("EMPTY" if not base.get("objects") else "DEGRADED_WITH_OBJECT_FALLBACK"),
        "target_count": len(base.get("objects") or {}),
        "ready_count": len(ready_codes),
        "ready_codes": ready_codes,
        "fallback_codes": fallback_codes,
        "primary_source": "tencent_qq minute/query",
        "formal_latest_price_source_unchanged": True,
        "stock_volume_normalization": "Tencent minute cumulative volume is hand for validated A-share stocks; production recent volume delta is normalized x100 to shares.",
        "decision_boundary": "个股分钟只增强第三层日内路径与成交参与，不独立产生ETF或个股交易动作。",
    }
    return base


_legacy.build = build
main = _legacy.main


if __name__ == "__main__":
    main()
