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

    formal_updated, formal_risk, source = max(candidates, key=lambda x: x[0])
    dashboard = (ROOT / "ETF当前状态_DASHBOARD.md").read_text(encoding="utf-8")
    match = __import__("re").search(r"\|ETF策略风险率\|约?\s*([+-]?\d+(?:\.\d+)?)%", dashboard)
    e2e = _read_json("data/state/e2e_status.json")
    risk_component = ((e2e.get("components") or {}).get("risk") or {})
    e2e_risk = risk_component.get("etf_strategy_risk_pct")
    e2e_source_updated_raw = risk_component.get("source_updated_at") or e2e.get("generated_at")
    try:
        e2e_source_updated = datetime.fromisoformat(str(e2e_source_updated_raw).replace("Z", "+00:00")) if e2e_source_updated_raw else None
    except (TypeError, ValueError):
        e2e_source_updated = None

    dashboard_risk = float(match.group(1)) if match else None
    mismatches = []
    notes = []
    if dashboard_risk is None or abs(dashboard_risk - formal_risk) > 0.03:
        mismatches.append(f"dashboard={dashboard_risk}")

    # e2e_status is a derived state and is rebuilt later in system-consistency.yml.
    # A newer formal review must not be rejected by a pre-rebuild stale E2E cache.
    # Once E2E's own source timestamp is at least as new as the formal review,
    # the equality check is strict again.
    e2e_is_current = bool(e2e_source_updated and e2e_source_updated >= formal_updated)
    if e2e_is_current:
        try:
            if e2e_risk is None or abs(float(e2e_risk) - formal_risk) > 0.03:
                mismatches.append(f"e2e={e2e_risk}")
        except (TypeError, ValueError):
            mismatches.append(f"e2e={e2e_risk}")
    else:
        notes.append(f"pre_rebuild_e2e_stale={e2e_risk}@{e2e_source_updated_raw or 'MISSING'}")

    detail = f"formal={formal_risk:.4f} source={source} updated={formal_updated.isoformat()}"
    if mismatches:
        detail += " " + " ".join(mismatches)
    elif notes:
        detail += " dashboard_aligned " + " ".join(notes)
    else:
        detail += " dashboard/e2e aligned"
    report.setdefault("checks", []).append({"name": "risk:formal_precedence", "status": "FAIL" if mismatches else "PASS", "detail": detail})
    if mismatches:
        message = "risk:formal_precedence:" + ";".join(mismatches)
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
        report["hard_error_count"] = len(report["errors"])
        report["status"] = "FAIL"


def _validate_trade_event_formal_sync(report: dict) -> None:
    """Require every executed trade event to be visible in both human formal records.

    For trade events created from 2026-08-27 onward, also require the event marker
    in Experience §2.1, the declared unique human-readable transaction index.
    This prevents a machine event / Dashboard update from silently outrunning the
    archive or experience record.
    """
    archive = (ROOT / "ETF市场行情档案_2026.md").read_text(encoding="utf-8")
    experience = (ROOT / "ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
    errors = []
    checked = 0
    trade_dir = ROOT / "events" / "trades"
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(event.get("execution_status") or "").upper() != "EXECUTED":
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        checked += 1
        if f"{event_id}｜" not in archive:
            errors.append(f"{event_id}:archive")
        if f"{event_id}｜" not in experience:
            errors.append(f"{event_id}:experience_case_intake")
        confirmed = str(event.get("confirmed_at_beijing") or "")[:10]
        if confirmed >= "2026-08-27":
            if f"TRADE_EVENT:{event_id}" not in experience:
                errors.append(f"{event_id}:experience_transaction_index")
            import re
            mapping = re.search(rf"^{re.escape(event_id)}｜- 已归入(CASE-\d{{8}}-\d{{2}})｜", experience, re.MULTILINE)
            if not mapping:
                errors.append(f"{event_id}:formal_case_mapping")
            elif mapping.group(1) not in experience or f"### " not in experience[:experience.find(mapping.group(1)) + 4]:
                errors.append(f"{event_id}:formal_case_section")
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({
        "name": "formal_files:executed_trade_event_sync",
        "status": status,
        "detail": f"executed_events_checked={checked} missing={errors}",
    })
    for item in errors:
        message = "formal_trade_sync:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")



def _validate_historical_trade_case_mapping(report: dict) -> None:
    """Require every canonical securities trade-index row to have exactly one valid CASE owner."""
    import re

    experience = (ROOT / "ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
    start_token = "### 2.1 2026-07-13以来完整证券成交索引"
    end_token = "### 2.2 银证转账与非交易现金流水"
    errors = []
    rows = []
    try:
        start = experience.index(start_token)
        section = experience[start:experience.index(end_token, start)]
    except ValueError:
        section = ""
        errors.append("transaction_index_section_missing")
    for line in section.splitlines():
        if not line.startswith("|2026-"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 10:
            errors.append("malformed_row=" + line[:80])
            continue
        rows.append(cols)
    headings = set(re.findall(r"^###\s+.*?(CASE-\d{8}-\d{2})[:：]", experience, re.MULTILINE))
    etf_count = 0
    stock_count = 0
    for cols in rows:
        dt, name, code, side, qty, price, principal, fee, cashflow, remark = cols[:10]
        case_ids = sorted(set(re.findall(r"CASE-\d{8}-\d{2}", remark)))
        if len(case_ids) != 1:
            errors.append(f"{dt}:{code}:case_count={len(case_ids)}")
        elif case_ids[0] not in headings:
            errors.append(f"{dt}:{code}:missing_case_heading={case_ids[0]}")
        if "ETF" in name:
            etf_count += 1
        else:
            stock_count += 1
    declared = re.search(r"共(\d+)笔证券交易：ETF\s*(\d+)笔、个股(\d+)笔", section)
    if declared:
        declared_counts = tuple(map(int, declared.groups()))
        actual_counts = (len(rows), etf_count, stock_count)
        if declared_counts != actual_counts:
            errors.append(f"declared_counts={declared_counts} actual={actual_counts}")
    else:
        errors.append("declared_trade_counts_missing")
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({
        "name": "formal_files:historical_trade_case_mapping",
        "status": status,
        "detail": f"trade_rows={len(rows)} etf={etf_count} stock={stock_count} missing={errors}",
    })
    for item in errors:
        message = "historical_trade_case_mapping:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")

def _validate_readme_front_door(report: dict) -> None:
    import re

    path = ROOT / "README.md"
    errors = []
    if not path.exists():
        errors.append("missing")
        readme = ""
    else:
        readme = path.read_text(encoding="utf-8")
    first_line = readme.splitlines()[0].strip() if readme.splitlines() else ""
    if first_line != "# ETF Trade System":
        errors.append(f"unexpected_h1={first_line}")
    if re.search(r"ETF Trade System\s+V\d+\.\d+\.\d+", readme, re.IGNORECASE):
        errors.append("hardcoded_system_version")
    required = [
        "ETF规则_MASTER.md",
        "ETF_SYSTEM_INDEX.md",
        "ETF当前状态_DASHBOARD.md",
        "ETF交易复盘与经验库_2026.md",
        "ETF市场行情档案_2026.md",
        "ETF与市场监测数据接口使用规范.md",
    ]
    missing_links = [name for name in required if name not in readme]
    if missing_links:
        errors.append("missing_links=" + ",".join(missing_links))
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({
        "name": "readme:canonical_front_door",
        "status": status,
        "detail": "README uses MASTER as sole formal version source" if not errors else ";".join(errors),
    })
    for item in errors:
        message = "readme_front_door:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")

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



def _validate_semantic_formal_structure(report: dict) -> None:
    from formal_document_structure import validate_files
    errors = validate_files(ROOT)
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({
        "name": "formal_files:semantic_structure",
        "status": status,
        "detail": "semantic chapter/order/managed-block placement valid" if not errors else "; ".join(errors),
    })
    for item in errors:
        message = "formal_semantic_structure:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")

def main() -> int:
    rc = core_main()
    report = _read_json("data/state/system_consistency.json")
    _normalize_stock_market_time_alignment(report)
    _validate_formal_risk_precedence(report)
    _validate_trade_event_formal_sync(report)
    _validate_historical_trade_case_mapping(report)
    _validate_semantic_formal_structure(report)
    _validate_readme_front_door(report)
    _validate_production_mutation_protocol(report)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "hard_error_count": report.get("hard_error_count"),
        "warning_count": report.get("warning_count"),
        "stock_market_time_alignment": next((x for x in report.get("checks", []) if x.get("name") == "stock_runtime:market_time_alignment"), {}),
        "formal_trade_event_sync": next((x for x in report.get("checks", []) if x.get("name") == "formal_files:executed_trade_event_sync"), {}),
        "readme_front_door": next((x for x in report.get("checks", []) if x.get("name") == "readme:canonical_front_door"), {}),
        "production_mutation_protocol": report.get("production_mutation_protocol") or {},
    }, ensure_ascii=False))
    return 1 if report.get("errors") else rc


if __name__ == "__main__":
    raise SystemExit(main())
