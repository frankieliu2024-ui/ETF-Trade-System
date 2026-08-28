from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from build_minute_path_features import quality_requirements
    from probe_index_minute_acceptance import _fetch_raw_points, acceptance_evidence
    from state_manager import now_utc, read_current, read_json
    from tencent_quote import fetch_tencent_quotes
    from build_account_stock_market import thscode
except ModuleNotFoundError:
    from scripts.build_minute_path_features import quality_requirements
    from scripts.probe_index_minute_acceptance import _fetch_raw_points, acceptance_evidence
    from scripts.state_manager import now_utc, read_current, read_json
    from scripts.tencent_quote import fetch_tencent_quotes
    from scripts.build_account_stock_market import thscode

ROOT = Path(__file__).resolve().parents[1]
BEIJING = ZoneInfo("Asia/Shanghai")


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _continuity(points: list[dict]) -> tuple[bool, list[int]]:
    gaps: list[int] = []
    for a, b in zip(points, points[1:]):
        delta = int((b["dt"] - a["dt"]).total_seconds() // 60)
        # Morning close -> afternoon open is the expected A-share lunch break.
        if a["dt"].strftime("%H%M") == "1130" and b["dt"].strftime("%H%M") == "1300":
            continue
        if delta != 1:
            gaps.append(delta)
    return not gaps, gaps


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    market_date = str(current.get("market_date") or "")
    stock_context = read_json(root / "data/state/stock_context.json", {})
    stocks = (stock_context.get("default_stock_layer") or {}).get("detected_non_etf_stocks") or []
    stocks = [x for x in stocks if str(x.get("code") or "").isdigit() and len(str(x.get("code") or "")) == 6]
    symbols = [thscode(str(x.get("code"))) for x in stocks]
    quotes = fetch_tencent_quotes(symbols, timeout=6) if symbols else {}
    req = quality_requirements(root)
    items = []
    for stock in stocks:
        code = str(stock.get("code") or "")
        name = str(stock.get("name") or "")
        symbol = thscode(code)
        try:
            raw_points = _fetch_raw_points(symbol, market_date)
            quote = quotes.get(symbol.upper()) or {}
            provider_ts = quote.get("provider_timestamp_ms")
            cutoff = datetime.fromtimestamp(float(provider_ts) / 1000.0, tz=BEIJING) if provider_ts is not None else None
            points = [x for x in raw_points if cutoff is None or x["dt"] <= cutoff]
            excluded = len(raw_points) - len(points)
            continuity_pass, gap_minutes = _continuity(points)
            latest = points[-1] if points else None
            quote_price = _f(quote.get("last_price"))
            minute_price = _f(latest.get("price")) if latest else None
            price_dev = abs(minute_price / quote_price - 1.0) * 100.0 if minute_price is not None and quote_price not in (None, 0) else None
            quote_volume = _f(quote.get("volume"))
            quote_amount = _f(quote.get("turnover"))
            minute_volume = _f(latest.get("cum_volume")) if latest else None
            minute_amount = _f(latest.get("cum_amount")) if latest else None
            volume_ratio = minute_volume / quote_volume if minute_volume is not None and quote_volume not in (None, 0) else None
            amount_ratio = minute_amount / quote_amount if minute_amount is not None and quote_amount not in (None, 0) else None
            point_in_time_pass = bool(latest) and (cutoff is None or latest["dt"] <= cutoff)
            terminal_price_pass = price_dev is not None and price_dev <= float(req.get("quote_price_deviation_pct", 0.25))
            coverage_pass = len(points) >= 240
            core_pass = bool(points) and continuity_pass and point_in_time_pass and terminal_price_pass and coverage_pass
            items.append({
                "code": code,
                "name": name,
                "thscode": symbol,
                "asset_class": "A_SHARE_STOCK",
                "status": "READY" if core_pass else "DEGRADED",
                "sample_count": len(points),
                "raw_sample_count": len(raw_points),
                "first_as_of_beijing": points[0]["dt"].isoformat(timespec="seconds") if points else None,
                "latest_as_of_beijing": latest["dt"].isoformat(timespec="seconds") if latest else None,
                "quote_as_of_beijing": datetime.fromtimestamp(float(provider_ts) / 1000.0, tz=BEIJING).isoformat(timespec="seconds") if provider_ts is not None else None,
                "excluded_after_quote_count": excluded,
                "continuity": {"pass": continuity_pass, "unexpected_gap_minutes": gap_minutes},
                "point_in_time": {"pass": point_in_time_pass},
                "quote_alignment": {
                    "minute_price": minute_price,
                    "quote_price": quote_price,
                    "absolute_price_deviation_pct": round(price_dev, 6) if price_dev is not None else None,
                    "pass": terminal_price_pass,
                },
                "cumulative_field_semantics_probe": {
                    "minute_cum_volume_raw": minute_volume,
                    "quote_volume": quote_volume,
                    "minute_to_quote_volume_ratio": round(volume_ratio, 8) if volume_ratio is not None else None,
                    "minute_cum_amount_raw": minute_amount,
                    "quote_turnover": quote_amount,
                    "minute_to_quote_amount_ratio": round(amount_ratio, 8) if amount_ratio is not None else None,
                    "purpose": "Determine Tencent minute cumulative volume/amount units for A-share stocks before production interpretation.",
                },
                "ten_minute_acceptance": acceptance_evidence(points),
                "core_quality_pass": core_pass,
            })
        except Exception as exc:
            items.append({"code": code, "name": name, "thscode": symbol, "asset_class": "A_SHARE_STOCK", "status": "FAILED", "core_quality_pass": False, "error": str(exc)[-800:]})
    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "generated_at_beijing": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "market_date": market_date,
        "mode": "TENCENT_A_SHARE_STOCK_MINUTE_PRODUCTION_POC",
        "read_only": True,
        "target_count": len(stocks),
        "core_pass_count": sum(1 for x in items if x.get("core_quality_pass") is True),
        "all_core_pass": bool(items) and all(x.get("core_quality_pass") is True for x in items),
        "items": items,
        "decision_boundary": "PoC validates A-share stock minute coverage, PIT, terminal quote alignment and cumulative-field units only; it does not create trading permission or modify account facts.",
    }


def main() -> None:
    payload = build(ROOT)
    target = ROOT / "data/state/stock_minute_production_poc.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "target_count": payload["target_count"], "core_pass_count": payload["core_pass_count"], "all_core_pass": payload["all_core_pass"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
