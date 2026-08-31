from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CONTEXT_PATH = ROOT / "data" / "state" / "overseas_context.json"
NAVER_KOSPI_URL = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI"
SEOUL = ZoneInfo("Asia/Seoul")


def _number(value) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def derive_previous_close(row: dict) -> float | None:
    """Derive KOSPI previous close from Naver's explicit close-to-close delta.

    Naver exposes the current index level and the absolute difference from the
    previous close, with the direction in compareToPreviousPrice.name.  This is
    preferable to inferring the reference from the current-day open.
    """
    close = _number(row.get("closePriceRaw", row.get("closePrice")))
    delta = _number(row.get("compareToPreviousClosePriceRaw", row.get("compareToPreviousClosePrice")))
    direction = str(((row.get("compareToPreviousPrice") or {}).get("name") or "")).upper()
    if close is None or delta is None:
        return None
    delta = abs(delta)
    if direction == "RISING":
        previous = close - delta
    elif direction == "FALLING":
        previous = close + delta
    elif direction in {"UNCHANGED", "SAME", "FLAT"}:
        previous = close
    else:
        return None
    return round(previous, 8) if previous > 0 else None


def _signed_ratio(row: dict) -> float | None:
    ratio = _number(row.get("fluctuationsRatioRaw", row.get("fluctuationsRatio")))
    if ratio is None:
        return None
    direction = str(((row.get("compareToPreviousPrice") or {}).get("name") or "")).upper()
    if direction == "FALLING":
        return -abs(ratio)
    if direction == "RISING":
        return abs(ratio)
    if direction in {"UNCHANGED", "SAME", "FLAT"}:
        return 0.0
    return None


def enrich_context(context: dict, row: dict) -> tuple[dict, str]:
    obj = ((context.get("objects") or {}).get("KOSPI") or {})
    latest = obj.get("latest") or {}
    if obj.get("quality_status") != "PASS" or obj.get("provider") != "naver_finance":
        return context, "SKIPPED_NON_NAVER_OR_NOT_PASS"
    if _number(latest.get("previous_close")) is not None:
        return context, "ALREADY_PRESENT"

    current_market_date = str(latest.get("market_date_local") or "")
    raw_time = row.get("localTradedAt")
    if not current_market_date or not raw_time:
        return context, "DEGRADED_MISSING_DATE"
    provider_time = datetime.fromisoformat(str(raw_time))
    if provider_time.tzinfo is None:
        provider_time = provider_time.replace(tzinfo=SEOUL)
    if provider_time.astimezone(SEOUL).date().isoformat() != current_market_date:
        return context, "DEGRADED_DATE_MISMATCH"

    current_close = _number(latest.get("close"))
    row_close = _number(row.get("closePriceRaw", row.get("closePrice")))
    previous = derive_previous_close(row)
    if current_close is None or row_close is None or previous is None:
        return context, "DEGRADED_REFERENCE_UNAVAILABLE"
    # A second Naver request happens seconds after the context builder. Permit a
    # small live-market move, but reject a materially different observation.
    if abs(row_close / current_close - 1.0) * 100 > 0.30:
        return context, "DEGRADED_LIVE_PRICE_MISMATCH"

    derived_ratio = (row_close / previous - 1.0) * 100
    reported_ratio = _signed_ratio(row)
    if reported_ratio is not None and abs(derived_ratio - reported_ratio) > 0.08:
        return context, "DEGRADED_RATIO_MISMATCH"

    latest["previous_close"] = previous
    obj["previous_close_reference"] = previous
    obj["previous_close_reference_source"] = "naver_finance:compareToPreviousClosePrice"
    obj["previous_close_reference_market_date"] = current_market_date
    obj["previous_close_reference_provider_time"] = provider_time.isoformat(timespec="seconds")
    return context, "ENRICHED"


def main() -> None:
    context = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    obj = ((context.get("objects") or {}).get("KOSPI") or {})
    latest = obj.get("latest") or {}
    if obj.get("quality_status") != "PASS" or obj.get("provider") != "naver_finance" or _number(latest.get("previous_close")) is not None:
        print(json.dumps({"ok": True, "status": "NO_ENRICHMENT_NEEDED"}, ensure_ascii=False))
        return

    req = urllib.request.Request(NAVER_KOSPI_URL, headers={"User-Agent": "ETF-Trade-System/2.2.30"})
    with urllib.request.urlopen(req, timeout=12) as response:
        payload = json.load(response)
    rows = payload.get("datas") or []
    if len(rows) != 1:
        raise RuntimeError(f"Naver KOSPI expected one row, got {len(rows)}")

    context, status = enrich_context(context, rows[0])
    if status != "ENRICHED":
        raise RuntimeError(f"KOSPI previous-close enrichment failed: {status}")
    atomic_json_write(CONTEXT_PATH, context)
    previous = (((context.get("objects") or {}).get("KOSPI") or {}).get("latest") or {}).get("previous_close")
    print(json.dumps({"ok": True, "status": status, "previous_close": previous}, ensure_ascii=False))


if __name__ == "__main__":
    main()
