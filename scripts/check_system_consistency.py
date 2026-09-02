from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from check_production_mutation_protocol import run as run_mutation_protocol
from check_system_consistency_core import main as core_main

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "state" / "system_consistency.json"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _recount(report: dict) -> None:
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")


def _remove_error(report: dict, prefix: str) -> None:
    report["errors"] = [x for x in (report.get("errors") or []) if not str(x).startswith(prefix)]
    _recount(report)


def _normalize_us_phase_freshness(report: dict) -> None:
    """Validate the objects that are live in the current US market phase.

    NDX/SOX are cash-session indices. During PRE/POST they are legitimate
    SESSION_REFERENCE facts and must not be forced to look like live extended-
    hours quotes. QQQ/SOXX are the live extended-hours evidence in those phases.
    REGULAR remains strict on direct NDX/SOX freshness.
    """
    target = next((x for x in report.get("checks", []) if x.get("name") == "us_extended:live_freshness"), None)
    if not target or target.get("status") != "FAIL":
        return
    context = _read_json("data/state/us_extended_hours_context.json")
    objects = context.get("objects") or {}
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    minute = now_et.hour * 60 + now_et.minute
    if now_et.weekday() >= 5:
        return
    if 4 * 60 <= minute < 9 * 60 + 30:
        phase, symbols = "PRE_MARKET", ("QQQ", "SOXX")
    elif 9 * 60 + 30 <= minute < 16 * 60:
        phase, symbols = "REGULAR", ("NDX", "SOX")
    elif 16 * 60 <= minute < 20 * 60:
        phase, symbols = "POST_MARKET", ("QQQ", "SOXX")
    else:
        return
    fresh_limit = int(_read_json("config/runtime_policy.json").get("fresh_max_age_seconds", 900))
    ages = []
    for symbol in symbols:
        record = objects.get(symbol) or {}
        latest = record.get("latest") or {}
        stamp = latest.get("timestamp")
        if stamp is None:
            return
        if str(record.get("current_market_phase") or "") != phase:
            return
        if str(record.get("quality_status") or "").upper() not in {"PASS", "FRESH"}:
            return
        ages.append(max(0, int((now_utc - datetime.fromtimestamp(int(stamp), timezone.utc)).total_seconds())))
    if not ages or max(ages) > fresh_limit:
        return
    target["status"] = "PASS"
    target["detail"] = f"phase_aware phase={phase} live_objects={list(symbols)} max_age_seconds={max(ages)} limit={fresh_limit}; cash indices are session references outside REGULAR"
    _remove_error(report, "us_extended:live_freshness:")


def _normalize_a_share_off_window_market_date(report: dict) -> None:
    """Separate current runtime-attempt date from the last valid A-share fact.

    Before the new A-share session opens (or after its capture window), runtime
    health can truthfully say today's session gate was skipped while CURRENT and
    its snapshot still point to the previous valid close. That is not a market-
    date contradiction if the preserved snapshot identity is unchanged.
    """
    target = next((x for x in report.get("checks", []) if x.get("name") == "a_share_runtime:market_date_alignment"), None)
    if not target or target.get("status") != "FAIL":
        return
    runtime = _read_json("data/state/runtime_health.json")
    current = _read_json("data/state/CURRENT.json")
    if str(runtime.get("status") or "").upper() != "SKIPPED":
        return
    if str(runtime.get("failure_stage") or "") != "session_gate" or str(runtime.get("reason") or "") != "outside_a_share_capture_window":
        return
    if str(current.get("node_status") or "").upper() != "READY" or str(current.get("latest_valid_node") or "").lower() != "close":
        return
    snapshot_rel = str(current.get("latest_snapshot") or "")
    if not snapshot_rel:
        return
    snapshot_path = ROOT / snapshot_rel
    if not snapshot_path.exists():
        return
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    market_date = str(current.get("market_date") or "")
    if not market_date or str(snapshot.get("market_date") or "") != market_date:
        return
    runtime_snapshot = str(runtime.get("latest_snapshot") or "")
    if runtime_snapshot and runtime_snapshot != snapshot_rel:
        return
    target["status"] = "PASS"
    target["detail"] = f"off_window_attempt_date={runtime.get('market_date')} preserved_last_valid_market_date={market_date} snapshot={snapshot_rel}"
    _remove_error(report, "a_share_runtime:market_date_alignment:")


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
    _recount(report)


def _normalize_idempotent_close_skip(report: dict) -> None:
    """Accept only a proven idempotent close skip as healthy runtime state."""
    runtime = _read_json("data/state/runtime_health.json")
    current = _read_json("data/state/CURRENT.json")
    if str(runtime.get("status") or "").upper() != "SKIPPED":
        return
    if str(runtime.get("reason") or "") != "close_already_recorded":
        return
    if str(current.get("node_status") or "").upper() != "READY":
        return
    if str(current.get("latest_valid_node") or "").lower() != "close":
        return
    market_date = str(current.get("market_date") or "")
    latest_snapshot = str(current.get("latest_snapshot") or "")
    if not market_date or not latest_snapshot:
        return
    if str(runtime.get("market_date") or "") != market_date:
        return
    if str(runtime.get("latest_snapshot") or "") != latest_snapshot:
        return
    snapshot_path = ROOT / latest_snapshot
    if not snapshot_path.exists():
        return
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if str(snapshot.get("market_date") or "") != market_date:
        return
    target = next((x for x in report.get("checks", []) if x.get("name") == "runtime_health:skipped_reason"), None)
    if not target or target.get("status") != "WARNING":
        return
    target["status"] = "PASS"
    target["detail"] = f"idempotent_close_skip reason=close_already_recorded snapshot={latest_snapshot}"
    prefix = "runtime_health:skipped_reason:"
    report["warnings"] = [x for x in (report.get("warnings") or []) if not str(x).startswith(prefix)]
    _recount(report)


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
        _recount(report)


def _validate_post_close_review_contract(report: dict) -> None:
    """Do not report a ready close context as complete without its canonical review."""
    try:
        context = _read_json("post_market_review/post_market_review_event.json")
    except (OSError, json.JSONDecodeError):
        return
    if not context.get("market_close") or str(context.get("status") or "").upper() != "READY_FOR_REVIEW":
        return
    market_date = str(context.get("market_date") or "")
    review_path = ROOT / "events" / "reviews" / f"{market_date}.json"
    closure_path = ROOT / "data" / "state" / f"close_review_closure_{market_date}.json"
    review = _read_json(f"events/reviews/{market_date}.json") if review_path.exists() else {}
    closure = _read_json(f"data/state/close_review_closure_{market_date}.json") if closure_path.exists() else {}
    errors = []
    if review.get("event_type") != "FORMAL_POST_CLOSE_REVIEW" or not isinstance(review.get("review"), dict):
        errors.append(f"missing_or_invalid_review=events/reviews/{market_date}.json")
    if closure.get("status") != "CLOSED" or closure.get("formal_review_path") != f"events/reviews/{market_date}.json":
        errors.append(f"missing_or_invalid_closure=data/state/close_review_closure_{market_date}.json")
    current = _read_json("data/state/CURRENT.json")
    pointer = current.get("close_review_closure") or {}
    if pointer.get("market_date") != market_date or pointer.get("formal_review_path") != f"events/reviews/{market_date}.json":
        errors.append("CURRENT.close_review_closure_is_stale")
    report.setdefault("checks", []).append({"name": "review:post_close_canonical_chain", "status": "FAIL" if errors else "PASS", "detail": "canonical post-close review, closure and CURRENT pointer are aligned" if not errors else "; ".join(errors)})
    for item in errors:
        message = "post_close_review:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    _recount(report)


def _case_mapping_required(current: dict, event: dict) -> bool:
    """Require final CASE ownership only when the trade's review node is due."""
    phase = str((current.get("data_freshness") or {}).get("market_phase") or current.get("market_phase") or "").upper()
    node = str(current.get("latest_valid_node") or "").lower()
    if "POST_CLOSE" in phase or phase in {"CLOSED", "CLOSE", "OUTSIDE_SESSION"} or node in {"close", "1500"}:
        return True
    event_date = str(event.get("confirmed_at_beijing") or event.get("executed_at_beijing") or event.get("event_id") or "")[:10]
    current_date = str(current.get("market_date") or "")
    return not current_date or not event_date or event_date != current_date


def _valid_unrecoverable_review_terminal(event_id: str, event: dict) -> bool:
    """Accept only the canonical lifecycle terminal projection, never a free-form flag."""
    market_date = str(event.get("execution_date") or event.get("confirmed_at_beijing") or "")[:10]
    if not market_date:
        return False
    path = ROOT / "events" / "reviews" / f"{market_date}.json"
    if not path.exists():
        return False
    try:
        from review_prerequisite_lifecycle import is_valid_unrecoverable_review_event
    except ModuleNotFoundError:
        from scripts.review_prerequisite_lifecycle import is_valid_unrecoverable_review_event
    try:
        review_event = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return is_valid_unrecoverable_review_event(review_event, event_id)


def _validate_trade_event_formal_sync(report: dict) -> None:
    """Require formal visibility, with lifecycle-aware final CASE timing."""
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
            if _case_mapping_required(_read_json("data/state/CURRENT.json"), event):
                terminal = _valid_unrecoverable_review_terminal(event_id, event)
                if not mapping and not terminal:
                    errors.append(f"{event_id}:formal_case_mapping")
                elif mapping and terminal:
                    errors.append(f"{event_id}:normal_case_conflicts_with_unrecoverable_terminal")
                elif mapping and mapping.group(1) not in experience or (mapping and f"### " not in experience[:experience.find(mapping.group(1)) + 4]):
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
    _recount(report)


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
        if len(case_ids) == 0 and not _case_mapping_required(
            _read_json("data/state/CURRENT.json"),
            {"event_id": f"{dt}:{code}", "confirmed_at_beijing": dt},
        ):
            # Same-day intraday transaction-index rows may remain pending until
            # the formal post-close review node.
            pass
        elif len(case_ids) != 1:
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
    _recount(report)


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
    _recount(report)


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
    _recount(report)


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
    _recount(report)


def main() -> int:
    core_main()
    report = _read_json("data/state/system_consistency.json")
    _normalize_us_phase_freshness(report)
    _normalize_a_share_off_window_market_date(report)
    _normalize_stock_market_time_alignment(report)
    _normalize_idempotent_close_skip(report)
    _validate_formal_risk_precedence(report)
    _validate_post_close_review_contract(report)
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
        "us_phase_freshness": next((x for x in report.get("checks", []) if x.get("name") == "us_extended:live_freshness"), {}),
        "a_share_market_date_alignment": next((x for x in report.get("checks", []) if x.get("name") == "a_share_runtime:market_date_alignment"), {}),
        "stock_market_time_alignment": next((x for x in report.get("checks", []) if x.get("name") == "stock_runtime:market_time_alignment"), {}),
        "formal_trade_event_sync": next((x for x in report.get("checks", []) if x.get("name") == "formal_files:executed_trade_event_sync"), {}),
        "readme_front_door": next((x for x in report.get("checks", []) if x.get("name") == "readme:canonical_front_door"), {}),
        "production_mutation_protocol": report.get("production_mutation_protocol") or {},
    }, ensure_ascii=False))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())

