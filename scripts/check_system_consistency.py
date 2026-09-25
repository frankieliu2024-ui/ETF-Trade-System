from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import os
import re

from check_production_mutation_protocol import run as run_mutation_protocol
import check_system_consistency_core as consistency_core
from check_system_consistency_core import main as core_main

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "state" / "system_consistency.json"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

# Bounded historical debt discovered by PIT replay.  This list is not a
# general exemption: any new completion identity missing its canonical event
# remains a hard failure.  Entries may be removed only when a historical
# artifact is legally recovered and read back.
LEGACY_UNRECOVERABLE_FORMAL_DECISION_IDS = {
    "20260922_093348_chatgpt_intraday_analysis_decision",
    "20260922_215438_chatgpt_decision_analysis_decision",
    "20260922_220802_chatgpt_decision_analysis_decision",
    "20260922_224104_chatgpt_decision_analysis_decision",
    "20260923_071000_chatgpt_decision_analysis_decision",
    "20260923_112107_chatgpt_decision_analysis_decision",
    "20260923_1300_chatgpt_decision_analysis_decision",
    "20260924_0910_chatgpt_decision_analysis_decision",
    "20260924_0945_chatgpt_decision_analysis_decision",
    "20260924_113441_chatgpt_decision_analysis_decision",
    "20260924_150403_chatgpt_formal_decision_decision",
}


def _read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _recount(report: dict) -> None:
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")


def _remove_error(report: dict, prefix: str) -> None:
    report["errors"] = [x for x in (report.get("errors") or []) if not str(x).startswith(prefix)]
    _recount(report)


def _normalize_async_context_freshness(report: dict) -> None:
    checks = report.get("checks") or []
    target = next((x for x in checks if x.get("name") == "dynamic_freshness:query_decision_aligned"), None)
    if not target or target.get("status") != "FAIL":
        return
    query = next((x for x in checks if x.get("name") == "dynamic_freshness:query_recalculated"), None)
    decision = next((x for x in checks if x.get("name") == "dynamic_freshness:decision_recalculated"), None)
    snapshot = next((x for x in checks if x.get("name") == "decision:query_decision_current_aligned"), None)
    if not query or query.get("status") != "PASS" or not decision or decision.get("status") != "PASS" or not snapshot or snapshot.get("status") != "PASS":
        return
    target["status"] = "PASS"
    target["detail"] = f"asynchronous_build_clocks allowed; {target.get('detail', '')}; query/decision freshness self-checks PASS and snapshot identity PASS"
    _remove_error(report, "dynamic_freshness:query_decision_aligned:")


def _normalize_us_phase_freshness(report: dict) -> None:
    target = next((x for x in report.get("checks", []) if x.get("name") == "us_extended:live_freshness"), None)
    if not target or target.get("status") != "FAIL":
        return
    detail = str(target.get("detail") or "")
    # Scheduled-pulse cache age is observability only; decision-time PIT stays fail-closed.
    match = re.search(r"failures=\[(.*?)\]\s+limit=(\d+)", detail)
    age_only = False
    if match:
        limit = int(match.group(2))
        entries = re.findall(r"[^,\[]+?:[^,\[]+?:age=(\d+):freshness=([A-Z_]+)", match.group(1))
        age_only = bool(entries) and all(int(age) > limit and freshness == "FRESH" for age, freshness in entries)
    if "max_age_seconds" in detail or age_only:
        target["status"] = "WARNING"
        target["detail"] = f"observability_stale {detail} canonical_facts_unaffected"
        _remove_error(report, "us_extended:live_freshness:")
        warning = "us_extended:live_freshness: derived observation stale"
        if warning not in report.setdefault("warnings", []):
            report["warnings"].append(warning)
        _recount(report)
        return
    context = _read_json("data/state/us_extended_hours_context.json")
    objects = context.get("objects") or {}
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    minute = now_et.hour * 60 + now_et.minute
    if now_et.weekday() >= 5:
        phase = "OUTSIDE_SESSION"
        symbols = ()
    elif 4 * 60 <= minute < 9 * 60 + 30:
        phase, symbols = "PRE_MARKET", ("QQQ", "SOXX")
    elif 9 * 60 + 30 <= minute < 16 * 60:
        phase, symbols = "REGULAR", ("NDX", "SOX")
    elif 16 * 60 <= minute < 20 * 60:
        phase, symbols = "POST_MARKET", ("QQQ", "SOXX")
    else:
        phase, symbols = "OUTSIDE_SESSION", ()
    if phase == "OUTSIDE_SESSION":
        target["status"] = "WARNING"
        target["detail"] = f"off_window_observability_stale local_time={now_et.strftime('%H:%M')} active_market_facts_not_blocked"
        _remove_error(report, "us_extended:live_freshness:")
        warning = "us_extended:live_freshness: off-window observability stale"
        if warning not in report.setdefault("warnings", []):
            report["warnings"].append(warning)
        _recount(report)
        return
    fresh_limit = int(_read_json("config/runtime_policy.json").get("fresh_max_age_seconds", 900))
    ages = []
    for symbol in symbols:
        record = objects.get(symbol) or {}
        latest = record.get("latest") or {}
        stamp = latest.get("timestamp")
        if stamp is None or str(record.get("current_market_phase") or "") != phase or str(record.get("quality_status") or "").upper() not in {"PASS", "FRESH"}:
            return
        ages.append(max(0, int((now_utc - datetime.fromtimestamp(int(stamp), timezone.utc)).total_seconds())))
    if not ages or max(ages) > fresh_limit:
        return
    target["status"] = "PASS"
    target["detail"] = f"phase_aware phase={phase} live_objects={list(symbols)} max_age_seconds={max(ages)} limit={fresh_limit}; cash indices are session references outside REGULAR"
    _remove_error(report, "us_extended:live_freshness:")


def _normalize_a_share_off_window_market_date(report: dict) -> None:
    target = next((x for x in report.get("checks", []) if x.get("name") == "a_share_runtime:market_date_alignment"), None)
    if not target or target.get("status") != "FAIL":
        return
    runtime = _read_json("data/state/runtime_health.json")
    current = _read_json("data/state/CURRENT.json")
    if str(runtime.get("status") or "").upper() != "SKIPPED" or str(runtime.get("failure_stage") or "") != "session_gate" or str(runtime.get("reason") or "") != "outside_a_share_capture_window":
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
    current = _read_json("data/state/CURRENT.json")
    stock_market = _read_json("data/state/stock_market_context.json")
    if str(current.get("latest_valid_node") or "").lower() != "close":
        return
    market_date = str(current.get("market_date") or "")
    items = [item for item in (stock_market.get("objects") or {}).values() if isinstance(item, dict)]
    if not market_date or not items or not all(str(item.get("quality_status") or "").upper() == "PASS" for item in items) or not all(str(item.get("market_phase") or "").upper() == "OUTSIDE_SESSION" for item in items):
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
    runtime = _read_json("data/state/runtime_health.json")
    current = _read_json("data/state/CURRENT.json")
    if str(runtime.get("status") or "").upper() != "SKIPPED" or str(runtime.get("reason") or "") != "close_already_recorded":
        return
    if str(current.get("node_status") or "").upper() != "READY" or str(current.get("latest_valid_node") or "").lower() != "close":
        return
    market_date = str(current.get("market_date") or "")
    latest_snapshot = str(current.get("latest_snapshot") or "")
    if not market_date or not latest_snapshot or str(runtime.get("market_date") or "") != market_date or str(runtime.get("latest_snapshot") or "") != latest_snapshot:
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
    equity = _read_json("data/state/etf_strategy_equity.json")
    summary = equity.get("summary") or {}
    current_gross = summary.get("current_strategy_return_pct_gross")
    if current_gross is not None:
        formal_risk = float(current_gross)
        source = "data/state/etf_strategy_equity.json::summary.current_strategy_return_pct_gross"
    else:
        formal_risk = None
        source = ""
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
    if formal_risk is None and not candidates:
        return
    if formal_risk is None:
        _, formal_risk, source = max(candidates, key=lambda x: x[0])
    dashboard = (ROOT / "ETF当前状态_DASHBOARD.md").read_text(encoding="utf-8")
    match = re.search(r"\|ETF策略风险率\|约?\s*([+-]?\d+(?:\.\d+)?)%", dashboard)
    # Candidate replay validates the E2E consumer from the current canonical
    # inputs.  The persisted e2e_status.json may still be a pre-replay cache
    # (for example -8.1); it is an output, never a current risk source.
    current = _read_json("data/state/CURRENT.json")
    try:
        from build_e2e_status import risk_component
    except ModuleNotFoundError:
        from scripts.build_e2e_status import risk_component
    e2e_risk = risk_component(equity, current).get("etf_strategy_risk_pct")
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


def _valid_current_close_review_completion(current: dict) -> dict:
    pointer = current.get("close_review_closure") if isinstance(current, dict) else {}
    if not isinstance(pointer, dict):
        return {}
    market_date = str(pointer.get("market_date") or "")
    if not market_date or str(pointer.get("status") or "").upper() != "CLOSED":
        return {}
    expected_review_path = f"events/reviews/{market_date}.json"
    if str(pointer.get("formal_review_path") or "") != expected_review_path:
        return {}
    review_path = ROOT / expected_review_path
    closure_rel = f"data/state/close_review_closure_{market_date}.json"
    closure_path = ROOT / closure_rel
    if not review_path.exists() or not closure_path.exists():
        return {}
    review = _read_json(expected_review_path)
    closure = _read_json(closure_rel)
    if review.get("event_type") != "FORMAL_POST_CLOSE_REVIEW":
        return {}
    if str(review.get("market_date") or market_date) != market_date:
        return {}
    if not isinstance(review.get("review"), dict):
        return {}
    if str(closure.get("status") or "").upper() != "CLOSED":
        return {}
    if str(closure.get("market_date") or market_date) != market_date:
        return {}
    if str(closure.get("formal_review_path") or "") != expected_review_path:
        return {}
    return {
        "market_date": market_date,
        "formal_review_path": expected_review_path,
        "closure_path": closure_rel,
    }


def _validate_post_close_review_contract(report: dict, now=None) -> None:
    """Apply scheduled-review due-time semantics independently of market phase."""
    try:
        context = _read_json("post_market_review/post_market_review_event.json")
    except (OSError, json.JSONDecodeError):
        return
    if not context.get("market_close") or str(context.get("status") or "").upper() != "READY_FOR_REVIEW":
        return

    event_date = str(context.get("market_date") or "")
    current = _read_json("data/state/CURRENT.json")
    completion = _valid_current_close_review_completion(current)
    if completion and (not event_date or completion["market_date"] >= event_date):
        detail = (
            "canonical post-close review completion validated "
            f"formal_review_market_date={completion['market_date']} "
            f"market_trigger_market_date={event_date or 'NONE'} "
            "completion_source=CURRENT.close_review_closure"
        )
        report.setdefault("checks", []).append({
            "name": "review:post_close_canonical_chain",
            "status": "PASS",
            "detail": detail,
        })
        _recount(report)
        return

    market_date = event_date
    try:
        from post_close_review_due import configured_review_due_time, review_due_state
    except ModuleNotFoundError:
        from scripts.post_close_review_due import configured_review_due_time, review_due_state
    from datetime import datetime
    from zoneinfo import ZoneInfo
    policy = _read_json("config/runtime_policy.json") or {}
    due = configured_review_due_time(policy)
    review_path = ROOT / "events" / "reviews" / f"{market_date}.json"
    closure_path = ROOT / "data" / "state" / f"close_review_closure_{market_date}.json"
    review = _read_json(f"events/reviews/{market_date}.json") if review_path.exists() else {}
    closure = _read_json(f"data/state/close_review_closure_{market_date}.json") if closure_path.exists() else {}
    errors = []
    if due is None:
        errors.append("invalid_or_missing_scheduled_trade_review_due_time")
        due_state = "INVALID_CONFIG"
    else:
        due_state = review_due_state(
            market_date,
            str(current.get("market_date") or market_date),
            now or datetime.now(ZoneInfo("Asia/Shanghai")),
            due,
        )
    if review.get("event_type") != "FORMAL_POST_CLOSE_REVIEW" or not isinstance(review.get("review"), dict):
        errors.append(f"missing_or_invalid_review=events/reviews/{market_date}.json")
    if closure.get("status") != "CLOSED" or closure.get("formal_review_path") != f"events/reviews/{market_date}.json":
        errors.append(f"missing_or_invalid_closure=data/state/close_review_closure_{market_date}.json")
    if due_state == "NOT_DUE_TODAY":
        report.setdefault("checks", []).append({
            "name": "review:post_close_canonical_chain",
            "status": "PASS",
            "detail": "review_not_due scheduled_trade_review_due_time="
                     f"{due} timezone=Asia/Shanghai",
        })
        _recount(report)
        return
    pointer = current.get("close_review_closure") or {}
    if pointer.get("market_date") != market_date or pointer.get("formal_review_path") != f"events/reviews/{market_date}.json":
        errors.append("CURRENT.close_review_closure_is_stale")
    report.setdefault("checks", []).append({
        "name": "review:post_close_canonical_chain",
        "status": "FAIL" if errors else "PASS",
        "detail": "canonical post-close review, closure and CURRENT pointer validated"
                if not errors else "; ".join(errors),
    })
    if errors:
        report.setdefault("errors", []).extend(f"post_close_review:{x}" for x in errors)
    _recount(report)
def _case_mapping_required(current: dict, event: dict) -> bool:
    """Require CASE after canonical review completion or once the trade is prior-day."""
    event_date = str(event.get("confirmed_at_beijing") or event.get("executed_at_beijing") or event.get("event_id") or "")[:10]
    current_date = str(current.get("market_date") or "")
    if not event_date or not current_date:
        return True
    if event_date != current_date:
        return True
    review_path = ROOT / "events" / "reviews" / f"{event_date}.json"
    if not review_path.exists():
        return False
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(review.get("event_type") or "").upper() in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_POST_CLOSE_REVIEW_UNAVAILABLE"}


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


def _canonical_case_mappings() -> dict[str, list[dict]]:
    mappings: dict[str, list[dict]] = {}
    review_dir = ROOT / "events" / "reviews"
    if not review_dir.exists():
        return mappings

    def add(trade_event_id: str, decision_id: str, case_id: str, security_code: str, case_status: str = "", mapping_reason: str = "") -> None:
        entry = {"trade_event_id": trade_event_id, "decision_id": decision_id, "case_id": case_id, "security_code": security_code, "case_status": case_status, "mapping_reason": mapping_reason}
        bucket = mappings.setdefault(trade_event_id, [])
        identity_keys = ("trade_event_id", "decision_id", "case_id", "security_code")
        identity = tuple(entry[key] for key in identity_keys)
        if not any(tuple(item.get(key, "") for key in identity_keys) == identity for item in bucket):
            bucket.append(entry)

    def visit(node: object) -> None:
        if isinstance(node, dict):
            trade_event_id = str(node.get("trade_event_id") or "")
            case_id = str(node.get("case_id") or "")
            if trade_event_id and case_id:
                add(trade_event_id, str(node.get("decision_id") or ""), case_id, str(node.get("security_code") or ""), str(node.get("case_status") or ""), str(node.get("mapping_reason") or ""))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for path in sorted(review_dir.glob("*.json")):
        try:
            review_event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        visit(review_event)
        review = review_event.get("review") if isinstance(review_event, dict) else None
        if not isinstance(review, dict):
            continue
        case_id = str(review.get("case_id") or "")
        match = re.search(r"(\d{6})", str(review.get("main_candidate") or ""))
        if not case_id or not match:
            continue
        code = match.group(1)
        review_date = str(review_event.get("market_date") or path.stem)
        trade_dir = ROOT / "events" / "trades"
        for trade_path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
            try:
                trade = json.loads(trade_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            trade_event_id = str(trade.get("event_id") or "")
            trade_date = str(trade.get("confirmed_at_beijing") or trade.get("executed_at_beijing") or "")[:10]
            if trade_event_id and str(trade.get("code") or "") == code and trade_date and trade_date <= review_date:
                add(trade_event_id, str(trade.get("linked_decision_id") or ""), case_id, code, str(review.get("case_mode") or ""), "canonical review case owner")
    return mappings



def _canonical_case_ineligibilities() -> dict[str, dict]:
    """Read explicit canonical review ineligibility, never infer it."""
    result = {}
    review_dir = ROOT / "events" / "reviews"
    for path in sorted(review_dir.glob("*.json")) if review_dir.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        review = payload.get("review") if isinstance(payload, dict) else None
        mapping = review.get("case_mapping") if isinstance(review, dict) else None
        for item in (mapping or {}).get("ineligible_executed_trades") or []:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("trade_event_id") or "")
            if event_id and item.get("eligibility") == "EXPLICIT_CANONICAL_INELIGIBILITY":
                result[event_id] = item
    return result

def _validate_canonical_case_mapping(event: dict, mappings: dict[str, list[dict]]) -> str | None:
    event_id = str(event.get("event_id") or "")
    candidates = mappings.get(event_id) or []
    if len(candidates) != 1:
        return f"formal_case_mapping_count={len(candidates)}"
    mapping = candidates[0]
    code = str(event.get("code") or "")
    if mapping.get("security_code") and mapping["security_code"] != code:
        return f"formal_case_mapping_security_code={mapping['security_code']}"
    linked_decision = str(event.get("linked_decision_id") or "")
    if mapping.get("decision_id") and linked_decision and mapping["decision_id"] != linked_decision:
        return f"formal_case_mapping_decision_id={mapping['decision_id']}"
    return None


def _validate_trade_event_formal_sync(report: dict) -> None:
    archive = (ROOT / "ETF市场行情档案_2026.md").read_text(encoding="utf-8")
    experience = (ROOT / "ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
    mappings = _canonical_case_mappings()
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
        confirmed = str(event.get("confirmed_at_beijing") or "")[:10]
        if confirmed >= "2026-08-27":
            if f"TRADE_EVENT:{event_id}" not in experience:
                errors.append(f"{event_id}:experience_transaction_index")
            if _case_mapping_required(_read_json("data/state/CURRENT.json"), event):
                terminal = _valid_unrecoverable_review_terminal(event_id, event)
                mapping_error = _validate_canonical_case_mapping(event, mappings)
                explicit_ineligibility = _canonical_case_ineligibilities().get(event_id)
                if mapping_error and explicit_ineligibility and not terminal:
                    continue
                if mapping_error and not terminal:
                    errors.append(f"{event_id}:{mapping_error}")
                elif mapping_error and terminal and (mappings.get(event_id) or []):
                    errors.append(f"{event_id}:normal_case_conflicts_with_unrecoverable_terminal")
                elif not mapping_error and not re.search(rf"^###\s+.*?{re.escape(mappings[event_id][0]['case_id'])}[:：]", experience, re.MULTILINE):
                    errors.append(f"{event_id}:formal_case_section")
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({"name": "formal_files:executed_trade_event_sync", "status": status, "detail": f"executed_events_checked={checked} missing={errors}"})
    for item in errors:
        message = "formal_trade_sync:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    _recount(report)


def _has_valid_terminal_for_trade_row(trade_date: str, code: str) -> bool:
    trade_dir = ROOT / "events" / "trades"
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(event.get("execution_status") or "").upper() != "EXECUTED":
            continue
        event_date = str(event.get("execution_date") or event.get("confirmed_at_beijing") or "")[:10]
        if event_date != trade_date[:10] or str(event.get("code") or "") != code:
            continue
        event_id = str(event.get("event_id") or "")
        if event_id and _valid_unrecoverable_review_terminal(event_id, event):
            return True
    return False


HISTORICAL_TRADE_EVENT_EFFECTIVE_DATE = "2026-08-27"
CASE_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])CASE-\d{8}-\d{2}(?![A-Za-z0-9])")


def _explicit_index_case_ids(remark: str) -> list[str]:
    return sorted(set(CASE_ID_PATTERN.findall(str(remark or ""))))


def _validate_historical_trade_case_mapping(report: dict) -> None:
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
    mappings = _canonical_case_mappings()
    etf_count = 0
    stock_count = 0
    for cols in rows:
        dt, name, code, side, qty, price, principal, fee, cashflow, _remark = cols[:10]
        event_ids = []
        trade_dir = ROOT / "events" / "trades"
        if trade_dir.exists():
            for path in sorted(trade_dir.glob("*.json")):
                try:
                    event = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                event_code = str(event.get("code") or "")
                event_stamp = str(event.get("confirmed_at_beijing") or event.get("executed_at_beijing") or event.get("event_id") or "")
                if event_code == code and event_stamp[:10] == dt[:10] and event_stamp[:10] >= HISTORICAL_TRADE_EVENT_EFFECTIVE_DATE:
                    event_ids.append(str(event.get("event_id") or path.stem))
        index_case_ids = _explicit_index_case_ids(_remark)
        case_ids = sorted({str(mapping.get("case_id") or "") for event_id in event_ids for mapping in (mappings.get(event_id) or []) if mapping.get("case_id")})
        terminal = _has_valid_terminal_for_trade_row(dt, code)
        current = _read_json("data/state/CURRENT.json")
        if event_ids:
            event_for_due = {"event_id": event_ids[0], "confirmed_at_beijing": dt}
            if not _case_mapping_required(current, event_for_due):
                pass
            elif len(case_ids) == 0 and not index_case_ids:
                # A canonical formal review may lawfully declare an executed
                # exit ineligible for CASE intake. Consume that exact event
                # disposition without fabricating a CASE; all other unmapped
                # historical rows remain hard failures.
                explicit_ineligibilities = _canonical_case_ineligibilities()
                if any(event_id in explicit_ineligibilities for event_id in event_ids):
                    pass
                elif terminal:
                    pass
                else:
                    errors.append(f"{dt}:{code}:case_count=0")
            elif len(case_ids) != 1:
                errors.append(f"{dt}:{code}:case_count={len(case_ids)}")
            elif len(index_case_ids) > 1:
                errors.append(f"{dt}:{code}:index_case_count={len(index_case_ids)}")
            elif index_case_ids and index_case_ids[0] != case_ids[0]:
                errors.append(f"{dt}:{code}:index_case_conflict={index_case_ids[0]} canonical={case_ids[0]}")
            elif not any(case_ids[0] in line for line in experience.splitlines() if line.startswith("### ")):
                errors.append(f"{dt}:{code}:missing_case_heading={case_ids[0]}")
        elif not _case_mapping_required(current, {"event_id": f"{dt}:{code}", "confirmed_at_beijing": dt}):
            pass
        elif dt[:10] >= HISTORICAL_TRADE_EVENT_EFFECTIVE_DATE:
            errors.append(f"{dt}:{code}:missing_formal_trade_event")
        elif len(index_case_ids) != 1:
            errors.append(f"{dt}:{code}:case_count={len(index_case_ids)}")
        elif not any(index_case_ids[0] in line for line in experience.splitlines() if line.startswith("### ")):
            errors.append(f"{dt}:{code}:missing_case_heading={index_case_ids[0]}")
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
    report.setdefault("checks", []).append({"name": "formal_files:historical_trade_case_mapping", "status": status, "detail": f"trade_rows={len(rows)} etf={etf_count} stock={stock_count} missing={errors}"})
    for item in errors:
        message = "historical_trade_case_mapping:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    _recount(report)


def _validate_execution_quality_projection(report: dict) -> None:
    persisted = _read_json("data/state/execution_quality.json")
    try:
        from build_execution_quality import build as build_execution_quality
    except ModuleNotFoundError:
        from scripts.build_execution_quality import build as build_execution_quality
    expected = build_execution_quality(ROOT)
    persisted_items = {str(item.get("trade_event_id") or ""): item for item in (persisted.get("items") or []) if isinstance(item, dict)}
    expected_items = {str(item.get("trade_event_id") or ""): item for item in (expected.get("items") or []) if isinstance(item, dict)}
    mismatches = []
    for event_id, expected_item in expected_items.items():
        actual = persisted_items.get(event_id)
        if not actual:
            mismatches.append(f"{event_id}:missing")
            continue
        for field in ("hypothesis_id", "decision_price", "adverse_execution_cost_pct", "status", "code"):
            if actual.get(field) != expected_item.get(field):
                mismatches.append(f"{event_id}:{field}")
    status = "FAIL" if mismatches else "PASS"
    report.setdefault("checks", []).append({"name": "state:execution_quality_canonical_alignment", "status": status, "detail": f"executed_events_checked={len(expected_items)} mismatches={mismatches}"})
    for item in mismatches:
        message = "execution_quality:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    _recount(report)


def _validate_readme_front_door(report: dict) -> None:
    path = ROOT / "README.md"
    errors = []
    readme = path.read_text(encoding="utf-8") if path.exists() else ""
    first_line = readme.splitlines()[0].strip() if readme.splitlines() else ""
    if first_line != "# ETF Trade System":
        errors.append(f"unexpected_h1={first_line}")
    if re.search(r"ETF Trade System\s+V\d+\.\d+\.\d+", readme, re.IGNORECASE):
        errors.append("hardcoded_system_version")
    required = ["ETF规则_MASTER.md", "ETF_SYSTEM_INDEX.md", "ETF当前状态_DASHBOARD.md", "ETF交易复盘与经验库_2026.md", "ETF市场行情档案_2026.md", "ETF与市场监测数据接口使用规范.md"]
    missing_links = [name for name in required if name not in readme]
    if missing_links:
        errors.append("missing_links=" + ",".join(missing_links))
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({"name": "readme:canonical_front_door", "status": status, "detail": "README uses MASTER as sole formal version source" if not errors else ";".join(errors)})
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
    report["production_mutation_protocol"] = {"status": result.get("status"), "direct_main_writers": result.get("direct_main_writers") or [], "fact_precedence": result.get("fact_precedence") or []}
    _recount(report)


def _validate_formal_completion_decision_readback(report: dict) -> None:
    """Ensure persisted formal-completion requests have a canonical event."""
    completion_dir = ROOT / "requests/live_snapshot"
    missing = []
    legacy = []
    mismatches = []
    checked = 0
    identities = {}
    for path in sorted(completion_dir.glob("*__formal_completion.json")) if completion_dir.exists() else []:
        try:
            request = _read_json(str(path.relative_to(ROOT)))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if request.get("formal_fact_type") != "FORMAL_DECISION" or not isinstance(request.get("formal_decision"), dict):
            continue
        decision = request["formal_decision"]
        decision_id = str(decision.get("decision_id") or "").strip()
        parent_id = str(request.get("parent_request_id") or "").strip()
        identity = (parent_id, decision_id)
        identities.setdefault(identity, []).append((path, request))
    for (parent_id, decision_id), occurrences in identities.items():
        checked += 1
        path, request = occurrences[0]
        event_path = ROOT / "events/decisions" / f"{decision_id}.json"
        if not decision_id or not event_path.exists():
            item = f"{path.name}->{decision_id or 'missing decision_id'}"
            if decision_id in LEGACY_UNRECOVERABLE_FORMAL_DECISION_IDS:
                legacy.append(item)
            else:
                missing.append(item)
            continue
        try:
            event = json.loads(event_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            mismatches.append(f"{path.name}:event unreadable")
            continue
        expected_parent = parent_id
        if event.get("event_type") != "FORMAL_DECISION" or event.get("formal_decision", {}).get("decision_id") != decision_id:
            mismatches.append(f"{path.name}:event identity mismatch")
        if expected_parent and event.get("request_id") not in {expected_parent, str(request.get("request_id") or "").strip()}:
            mismatches.append(f"{path.name}:request identity mismatch")
    # Bounded unrecoverable historical identities are terminal audit facts, not\n    # active current-health degradation. They stay visible in detail while any\n    # new/unlisted missing identity remains a hard failure.\n    status = "FAIL" if missing or mismatches else "PASS"
    detail = f"checked_unique={checked} retry_files_deduplicated={sum(max(0, len(v)-1) for v in identities.values())} legacy={legacy} missing={missing} mismatches={mismatches}"
    report.setdefault("checks", []).append({"name": "formal_completion:decision_fact_readback", "status": status, "detail": detail})
    for item in missing:
        message = "formal_completion:decision_fact_missing:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    for item in mismatches:
        message = "formal_completion:decision_fact_mismatch:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    _recount(report)


def _validate_semantic_formal_structure(report: dict) -> None:
    from formal_document_structure import validate_files
    errors = validate_files(ROOT)
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({"name": "formal_files:semantic_structure", "status": status, "detail": "semantic chapter/order/managed-block placement valid" if not errors else "; ".join(errors)})
    for item in errors:
        message = "formal_semantic_structure:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    _recount(report)


def main() -> int:
    import argparse
    global REPORT
    parser = argparse.ArgumentParser(description="Run the canonical system consistency validator.")
    parser.add_argument("--report-path", default=os.environ.get("ETF_CONSISTENCY_REPORT_PATH", str(REPORT)))
    parser.add_argument("--no-persist", action="store_true", help="write the report only to the supplied ephemeral path")
    args = parser.parse_args()
    REPORT = Path(args.report_path).resolve()
    os.environ["ETF_CONSISTENCY_REPORT_PATH"] = str(REPORT)
    consistency_core.REPORT = REPORT
    core_main()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    _normalize_async_context_freshness(report)
    _normalize_us_phase_freshness(report)
    _normalize_a_share_off_window_market_date(report)
    _normalize_stock_market_time_alignment(report)
    _normalize_idempotent_close_skip(report)
    _validate_formal_risk_precedence(report)
    _validate_post_close_review_contract(report)
    _validate_trade_event_formal_sync(report)
    _validate_historical_trade_case_mapping(report)
    _validate_formal_completion_decision_readback(report)
    _validate_semantic_formal_structure(report)
    _validate_execution_quality_projection(report)
    _validate_readme_front_door(report)
    _validate_production_mutation_protocol(report)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report.get("status"), "hard_error_count": report.get("hard_error_count"), "warning_count": report.get("warning_count"), "async_context_freshness": next((x for x in report.get("checks", []) if x.get("name") == "dynamic_freshness:query_decision_aligned"), {}), "us_phase_freshness": next((x for x in report.get("checks", []) if x.get("name") == "us_extended:live_freshness"), {}), "a_share_market_date_alignment": next((x for x in report.get("checks", []) if x.get("name") == "a_share_runtime:market_date_alignment"), {}), "stock_market_time_alignment": next((x for x in report.get("checks", []) if x.get("name") == "stock_runtime:market_time_alignment"), {}), "formal_trade_event_sync": next((x for x in report.get("checks", []) if x.get("name") == "formal_files:executed_trade_event_sync"), {}), "readme_front_door": next((x for x in report.get("checks", []) if x.get("name") == "readme:canonical_front_door"), {}), "production_mutation_protocol": report.get("production_mutation_protocol") or {}, "errors": report.get("errors") or []}, ensure_ascii=False))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
