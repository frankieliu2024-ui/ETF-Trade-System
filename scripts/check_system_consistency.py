from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from check_system_consistency_core import main as core_main

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "state" / "system_consistency.json"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _normalize_stock_market_time_alignment(report: dict) -> None:
    """Remove only the false post-close stock timing warning.

    The 900-second alignment requirement remains unchanged for active-session
    comparisons. A larger gap is accepted only when CURRENT is a close node,
    every account-stock quote is a same-trading-day OUTSIDE_SESSION PASS fact,
    and therefore the provider timestamp correctly represents the last legal
    market quote rather than a stale live quote.
    """
    current = _read_json("data/state/CURRENT.json")
    stock_market = _read_json("data/state/stock_market_context.json")
    if str(current.get("latest_valid_node") or "").lower() != "close":
        return
    market_date = str(current.get("market_date") or "")
    items = [item for item in (stock_market.get("objects") or {}).values() if isinstance(item, dict)]
    if not market_date or not items:
        return
    if not all(str(item.get("quality_status") or "").upper() == "PASS" for item in items):
        return
    if not all(str(item.get("market_phase") or "").upper() == "OUTSIDE_SESSION" for item in items):
        return
    try:
        stock_dts = [datetime.fromisoformat(str(item["as_of_beijing"]).replace("Z", "+00:00")) for item in items]
        core_dt = datetime.fromisoformat(str(current.get("captured_at") or "").replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return
    if not all(dt.astimezone(SHANGHAI).date().isoformat() == market_date for dt in stock_dts):
        return
    max_delay = max(round((core_dt - dt).total_seconds()) for dt in stock_dts)
    target = next((x for x in report.get("checks", []) if x.get("name") == "stock_runtime:market_time_alignment"), None)
    if not target or target.get("status") != "WARNING":
        return
    target["status"] = "PASS"
    target["detail"] = f"session_aligned_close_reference max_core_minus_stock_seconds={max_delay}"
    prefix = "stock_runtime:market_time_alignment:"
    report["warnings"] = [x for x in (report.get("warnings") or []) if not str(x).startswith(prefix)]
    report["warning_count"] = len(report["warnings"])
    if not report.get("errors"):
        report["status"] = "WARNING" if report["warnings"] else "PASS"


def main() -> int:
    rc = core_main()
    report = _read_json("data/state/system_consistency.json")
    _normalize_stock_market_time_alignment(report)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "hard_error_count": report.get("hard_error_count"),
        "warning_count": report.get("warning_count"),
        "stock_market_time_alignment": next((x for x in report.get("checks", []) if x.get("name") == "stock_runtime:market_time_alignment"), {}),
    }, ensure_ascii=False))
    return 1 if report.get("errors") else rc


if __name__ == "__main__":
    raise SystemExit(main())
