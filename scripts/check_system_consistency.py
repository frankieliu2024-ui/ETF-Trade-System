from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from check_production_mutation_protocol import run as run_mutation_protocol
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


def _validate_formal_risk_precedence(report: dict) -> None:
    review_dir = ROOT / "events" / "reviews"
    candidates = []
    for path in review_dir.glob("*.json") if review_dir.exists() else []:
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
            review = event.get("review") or event.get("formal_review") or {}
            fact = review.get("etf_strategy_known_net") or {}
            risk = float(fact.get("etf_strategy_risk_rate_pct"))
            updated = datetime.fromisoformat(str(event.get("updated_at_beijing") or event.get("account_updated_at")).replace("Z", "+00:00"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        candidates.append((updated, risk, str(path.relative_to(ROOT))))
    if not candidates:
        return
    _, formal_risk, source = max(candidates, key=lambda x: x[0])
    dashboard = (ROOT / "ETF当前状态_DASHBOARD.md").read_text(encoding="utf-8")
    match = __import__("re").search(r"\|ETF策略风险率\|约?\s*([+-]?\d+(?:\.\d+)?)%", dashboard)
    e2e = _read_json("data/state/e2e_status.json")
    e2e_risk = ((e2e.get("components") or {}).get("risk") or {}).get("etf_strategy_risk_pct")
    dashboard_risk = float(match.group(1)) if match else None
    mismatches = []
    if dashboard_risk is None or abs(dashboard_risk - formal_risk) > 0.03:
        mismatches.append(f"dashboard={dashboard_risk}")
    try:
        if e2e_risk is None or abs(float(e2e_risk) - formal_risk) > 0.03:
            mismatches.append(f"e2e={e2e_risk}")
    except (TypeError, ValueError):
        mismatches.append(f"e2e={e2e_risk}")
    report.setdefault("checks", []).append({"name": "risk:formal_precedence", "status": "FAIL" if mismatches else "PASS", "detail": f"formal={formal_risk:.4f} source={source} " + (" ".join(mismatches) if mismatches else "dashboard/e2e aligned")})
    if mismatches:
        message = "risk:formal_precedence:" + ";".join(mismatches)
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
        report["hard_error_count"] = len(report["errors"])
        report["status"] = "FAIL"


def _validate_production_mutation_protocol(report: dict) -> None:
    result = run_mutation_protocol(ROOT)
    for item in result.get("checks") or []:
        report.setdefault("checks", []).append(item)
    for message in result.get("errors") or []:
        normalized = "mutation_protocol:" + str(message)
        if normalized not in report.setdefault("errors", []):
            report["errors"].append(normalized)
    for message in result.get("warnings") or []:
        normalized = "mutation_protocol:" + str(message)
        if normalized not in report.setdefault("warnings", []):
            report["warnings"].append(normalized)
    report["production_mutation_protocol"] = {
        "status": result.get("status"),
        "direct_main_writers": result.get("direct_main_writers") or [],
        "fact_precedence": result.get("fact_precedence") or [],
    }
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")


def main() -> int:
    rc = core_main()
    report = _read_json("data/state/system_consistency.json")
    _normalize_stock_market_time_alignment(report)
    _validate_formal_risk_precedence(report)
    _validate_production_mutation_protocol(report)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "hard_error_count": report.get("hard_error_count"),
        "warning_count": report.get("warning_count"),
        "stock_market_time_alignment": next((x for x in report.get("checks", []) if x.get("name") == "stock_runtime:market_time_alignment"), {}),
        "production_mutation_protocol": report.get("production_mutation_protocol") or {},
    }, ensure_ascii=False))
    return 1 if report.get("errors") else rc


if __name__ == "__main__":
    raise SystemExit(main())
