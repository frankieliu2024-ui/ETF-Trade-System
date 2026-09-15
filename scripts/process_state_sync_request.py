from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median

from state_manager import atomic_json_write, build_etf_strategy_risk_metrics
try:
    from emergency_market_evidence import validate_external_market_evidence
except ModuleNotFoundError:
    from scripts.emergency_market_evidence import validate_external_market_evidence
try:
    from build_stock_context import active_account_asset_codes, build_managed_position_projection, first, normalize_code, position_metric
except ModuleNotFoundError:
    from scripts.build_stock_context import active_account_asset_codes, build_managed_position_projection, first, normalize_code, position_metric
try:
    from manual_formal_decision_identity import canonical_manual_identity
except ModuleNotFoundError:
    from scripts.manual_formal_decision_identity import canonical_manual_identity
from sync_formal_files import sync_formal_files
from formal_file_mutation_gateway import (
    append_managed_line,
    replace_managed_block as replace_block,
    upsert_formal_line,
    upsert_managed_line,
    write_formal_text_if_changed,
)
try:
    from lifecycle_state import build_lifecycle_projection
except ModuleNotFoundError:
    from scripts.lifecycle_state import build_lifecycle_projection
try:
    from review_prerequisite_lifecycle import (
        build_unrecoverable_review_event,
        terminal_experience_line,
        validate_unrecoverable_assessment,
    )
except ModuleNotFoundError:
    from scripts.review_prerequisite_lifecycle import (
        build_unrecoverable_review_event,
        terminal_experience_line,
        validate_unrecoverable_assessment,
    )

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
DASHBOARD = ROOT / "ETF当前状态_DASHBOARD.md"
ARCHIVE = ROOT / "ETF市场行情档案_2026.md"
EXPERIENCE = ROOT / "ETF交易复盘与经验库_2026.md"
ACCOUNT = ROOT / "data/state/account_fact.json"
CANONICAL_INGRESS_SUBMITTED = "CANONICAL_INGRESS_SUBMITTED"
CANONICAL_INGRESS_FAILED_EXPLICITLY = "CANONICAL_INGRESS_FAILED_EXPLICITLY"
CANONICAL_INGRESS_NOT_APPLICABLE = "CANONICAL_INGRESS_NOT_APPLICABLE"
FORMAL_OPPORTUNITY_STATUSES = {"无机会", "观察机会", "Trial机会", "Confirm机会"}
FORMAL_HOLDING_LIFECYCLES = {"持有管理", "降低风险", "退出"}
FORMAL_LIFECYCLE_COMPATIBILITY_TERMS = {
    "观察", "Trial", "Confirm", "持有", "持有管理", "持仓管理", "降低风险", "退出",
    "ACTIVE_TRIAL", "RESOLVED",
}
START = "<!-- AUTO_STATE_SYNC_START -->"
END = "<!-- AUTO_STATE_SYNC_END -->"
TRADE_START = "<!-- AUTO_TRADE_EVENTS_START -->"
TRADE_END = "<!-- AUTO_TRADE_EVENTS_END -->"
CASE_START = "<!-- AUTO_CASE_INTAKE_START -->"
CASE_END = "<!-- AUTO_CASE_INTAKE_END -->"
REVIEW_ARCHIVE_START = "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->"
REVIEW_ARCHIVE_END = "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->"
REVIEW_EXPERIENCE_START = "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->"
REVIEW_EXPERIENCE_END = "<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->"
CASE_DETAILS_START = "<!-- AUTO_CASE_DETAILS_START -->"
CASE_DETAILS_END = "<!-- AUTO_CASE_DETAILS_END -->"
REVIEW_UNAVAILABLE_START = "<!-- AUTO_REVIEW_PREREQUISITE_UNAVAILABLE_START -->"
REVIEW_UNAVAILABLE_END = "<!-- AUTO_REVIEW_PREREQUISITE_UNAVAILABLE_END -->"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_time(text: object) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)


def latest_formal_review_decision(root: Path) -> dict:
    review_dir = root / "events" / "reviews"
    candidates = []
    for path in review_dir.glob("*.json") if review_dir.exists() else []:
        try:
            event = load_json(path)
            review = event.get("review") or event.get("formal_review") or {}
            stamp = parse_time(review.get("reviewed_at_beijing") or event.get("updated_at_beijing") or event.get("account_updated_at"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not stamp or not isinstance(review, dict):
            continue
        lifecycle = review.get("lifecycle")
        if isinstance(lifecycle, dict):
            lifecycle = "；".join(f"{key}：{value}" for key, value in lifecycle.items())
        candidates.append((stamp, {
            "risk_permission": review.get("risk_permission") or "未提供",
            "lifecycle": lifecycle or "未提供",
            "main_candidate": review.get("main_candidate") or "无新的主候选。",
            "amount_action": review.get("action") or review.get("amount_action") or "未提供",
            "decisive_reason": review.get("zero_amount_decisive_reason") or review.get("decisive_reason") or "未提供",
            "data_as_of_beijing": (review.get("data_time") or {}).get("a_share_effective_close_beijing") or review.get("reviewed_at_beijing") or "未提供",
        }))
    return max(candidates, key=lambda item: item[0])[1] if candidates else {}


def pct(new, old):
    new_v, old_v = safe_float(new), safe_float(old)
    if new_v is None or old_v in (None, 0.0):
        return None
    return round((new_v / old_v - 1.0) * 100.0, 4)


def money(v: object) -> str:
    try:
        return f"{float(v):,.2f}元"
    except Exception:
        return "—"


def display_name(p: dict) -> str:
    return f"{p.get('name', '')}（{p.get('code', '')}）"


def _snapshot_fact_times(snapshot: dict) -> list[datetime]:
    values = []
    for value in (snapshot.get("provider_as_of"), snapshot.get("provider_as_of_beijing"), snapshot.get("as_of_beijing")):
        parsed = parse_time(value)
        if parsed is not None:
            values.append(parsed)
    for row in snapshot.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for key in ("as_of_beijing", "provider_as_of_beijing", "provider_timestamp"):
            parsed = parse_time(row.get(key))
            if parsed is not None:
                values.append(parsed)
    return values


def _snapshot_is_valid_for_decision(market_date: str, snapshot: dict, market_fact_cutoff: datetime, availability_cutoff: datetime) -> tuple[bool, str]:
    if snapshot.get("market_date") != market_date or snapshot.get("quality_status") != "PASS":
        return False, "SNAPSHOT_IDENTITY_OR_QUALITY_INVALID"
    captured = parse_time(snapshot.get("captured_at_beijing") or snapshot.get("captured_at"))
    if captured is None or captured > availability_cutoff:
        return False, "SNAPSHOT_NOT_AVAILABLE_BY_PERSISTENCE_BOUNDARY"
    if any(value > market_fact_cutoff for value in _snapshot_fact_times(snapshot)):
        return False, "SNAPSHOT_MARKET_FACT_AFTER_DECISION_CUTOFF"
    return True, ""


def select_point_in_time_snapshot(market_date: str, decision_time: str, *, availability_time: str = "", consumed_snapshot: str = "") -> tuple[str, dict, str]:
    market_fact_cutoff = parse_time(decision_time)
    availability_cutoff = parse_time(availability_time) or datetime.now(SHANGHAI)
    if not market_date or market_fact_cutoff is None:
        return "", {}, "NO_VALID_DECISION_TIME"
    candidates = []
    snapshot_dir = ROOT / "data/market/snapshots"
    if consumed_snapshot:
        candidate_path = (ROOT / consumed_snapshot).resolve()
        if ROOT not in candidate_path.parents or not candidate_path.exists():
            return "", {}, "CONSUMED_SNAPSHOT_REFERENCE_INVALID"
        paths = [candidate_path]
    else:
        paths = sorted(snapshot_dir.glob(f"{market_date}_*.json"))
    for path in paths:
        try:
            snap = load_json(path)
        except Exception:
            continue
        valid, reason = _snapshot_is_valid_for_decision(market_date, snap, market_fact_cutoff, availability_cutoff)
        if not valid:
            if consumed_snapshot:
                return "", {}, reason
            continue
        captured = parse_time(snap.get("captured_at_beijing") or snap.get("captured_at"))
        if captured is not None:
            candidates.append((captured, path, snap))
    if not candidates:
        return "", {}, "NO_PRIOR_SNAPSHOT"
    _, path, snap = max(candidates, key=lambda x: x[0])
    status = "CONSUMED_SNAPSHOT_VALIDATED" if consumed_snapshot else "POINT_IN_TIME_SNAPSHOT_TWO_CLOCK_VALIDATED"
    return str(path.relative_to(ROOT)).replace("\\", "/"), snap, status


def build_comparison_snapshot(snapshot: dict) -> dict:
    universe = load_json(ROOT / "config/market/etf_monitor_universe.json")
    names = {str(x.get("code")): str(x.get("name")) for x in (universe.get("objects") or []) if x.get("code")}
    rows = {str(x.get("symbol")): x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"}
    changes = [safe_float(rows[c].get("change_pct")) for c in names if c in rows]
    changes = [x for x in changes if x is not None]
    med = median(changes) if changes else None
    sh = safe_float((rows.get("000001") or {}).get("change_pct"))
    cyb = safe_float((rows.get("399006") or {}).get("change_pct"))
    items = []
    for code, name in names.items():
        row = rows.get(code)
        if not row:
            continue
        change = safe_float(row.get("change_pct"))
        items.append({"code": code, "name": name, "display_name": f"{name}（{code}）", "as_of_beijing": row.get("as_of_beijing"), "price": row.get("close"), "change_pct": change, "vs_universe_median_pct_points": round(change - med, 4) if change is not None and med is not None else None, "vs_shanghai_pct_points": round(change - sh, 4) if change is not None and sh is not None else None, "vs_chinext_pct_points": round(change - cyb, 4) if change is not None and cyb is not None else None})
    return {"as_of_beijing": snapshot.get("captured_at_beijing"), "market_phase": snapshot.get("market_phase"), "etf_count": len(items), "items": items}


def resolve_hypothesis_id(decision: dict, code: str, market_date: str, decision_id: str) -> tuple[str, str]:
    explicit = str(decision.get("hypothesis_id") or "").strip()
    if explicit:
        return explicit, "EXPLICIT"
    return "", "UNRESOLVED"


def validate_formal_decision_contract(decision: dict) -> str:
    opportunity_status = str(decision.get("opportunity_status") or "").strip()
    if opportunity_status not in FORMAL_OPPORTUNITY_STATUSES:
        return "opportunity_status must be one of: " + ", ".join(sorted(FORMAL_OPPORTUNITY_STATUSES))
    risk = decision.get("risk_permission")
    if risk is not None and str(risk).strip() not in {"禁止新增", "允许Trial", "允许Confirm"}:
        return "risk_permission is not a registered formal value"
    return ""


def _load_account_for_lifecycle_validation() -> dict:
    account_path = ROOT / "data" / "state" / "account_fact.json"
    return load_json(account_path) if account_path.exists() else {}


def validate_managed_position_lifecycle(value: object, account: dict, object_name: str = "lifecycle") -> str:
    return ""


def validate_managed_position_review_contract(value: object, account: dict, object_name: str = "managed_position_reviews") -> str:
    return ""


def record_formal_decision(request: dict) -> tuple[bool, str]:
    decision = request.get("formal_decision")
    if not isinstance(decision, dict) or not decision:
        return False, ""
    contract_error = validate_formal_decision_contract(decision)
    if contract_error:
        raise ValueError(f"invalid formal decision contract: {contract_error}")
    account_for_lifecycle = _load_account_for_lifecycle_validation()
    managed_error = validate_managed_position_lifecycle(decision.get("lifecycle"), account_for_lifecycle, "formal_decision.lifecycle")
    if managed_error:
        raise ValueError(f"invalid formal decision managed-position contract: {managed_error}")
    review_error = validate_managed_position_review_contract(decision.get("managed_position_reviews"), account_for_lifecycle, "formal_decision.managed_position_reviews")
    if review_error:
        raise ValueError(f"invalid formal decision managed-position review contract: {review_error}")
    managed_projection = build_managed_position_projection(ROOT, account_for_lifecycle)
    current_path = ROOT / "data/state/CURRENT.json"
    current = load_json(current_path) if current_path.exists() else {}
    market_date = str(request.get("market_date") or current.get("market_date") or "")
    main_candidate = str(decision.get("main_candidate") or "")
    explicit_code = str(decision.get("candidate_code") or decision.get("code") or "")
    match = re.search(r"（(\d{6})）", main_candidate) or re.search(r"(?<!\d)(\d{6})(?!\d)", main_candidate)
    code = explicit_code or (match.group(1) if match else "")
    name = str(decision.get("candidate_name") or "")
    if not name and code:
        name_match = re.search(rf"([^｜+，,；;]+?)（{re.escape(code)}）", main_candidate)
        if name_match:
            name = name_match.group(1).strip()
    request_id, parent_request_id, fingerprint = canonical_manual_identity(request, decision, market_date)
    decision_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(decision.get("decision_id") or (parent_request_id if parent_request_id else request_id) or f"{market_date}_{fingerprint[:12]}"))
    decision_time = str(decision.get("data_as_of_beijing") or "") or datetime.now(SHANGHAI).isoformat(timespec="seconds")
    availability_time = str(request.get("persistence_available_at_beijing") or request.get("requested_at_beijing") or "")
    consumed_snapshot = str(request.get("consumed_snapshot") or request.get("consumed_snapshot_path") or decision.get("consumed_snapshot") or decision.get("consumed_snapshot_path") or "")
    snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(market_date, decision_time, availability_time=availability_time, consumed_snapshot=consumed_snapshot)
    hypothesis_id, hypothesis_link_status = resolve_hypothesis_id(decision, code, market_date, decision_id)
    event = {
        "event_type": "FORMAL_DECISION",
        "decision_id": decision_id,
        "request_id": request_id,
        "parent_request_id": parent_request_id,
        "fingerprint": fingerprint,
        "market_date": market_date,
        "decision_time_beijing": decision_time,
        "interaction_scenario": request.get("interaction_scenario"),
        "candidate_code": code,
        "candidate_name": name,
        "hypothesis_id": hypothesis_id,
        "hypothesis_link_status": hypothesis_link_status,
        "price_source_snapshot": snapshot_rel,
        "point_in_time_status": pit_status,
        "comparison_snapshot": build_comparison_snapshot(snapshot) if snapshot else {"items": []},
        "formal_decision": decision,
        "managed_position_sell_review": managed_projection,
        "read_only_research_event": True,
        "recorded_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
    }
    event_path = ROOT / "events/decisions" / f"{decision_id}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    if event_path.exists():
        prior = load_json(event_path)
        if prior.get("fingerprint") == fingerprint and prior.get("price_source_snapshot") == snapshot_rel:
            return True, decision_id
    atomic_json_write(event_path, event)
    return True, decision_id


def canonical_ingress_contract_for_request(request: dict) -> dict:
    return {"required": False, "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE, "reason": "not_a_formal_fact_ingress_request"}


def sync_current_account_mirror(root: Path, account: dict) -> None:
    return None


def record_post_close_review(account: dict, request: dict) -> tuple[bool, bool]:
    return False, False


def sync_formal_files(root: Path, account: dict):
    return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    req_path = (ROOT / args.request_path).resolve()
    if ROOT not in req_path.parents or not req_path.exists():
        raise RuntimeError("invalid state sync request path")
    request = load_json(req_path)
    decision_recorded, decision_id = record_formal_decision(request)
    result = {"ok": True, "request_id": request.get("request_id"), "formal_decision_recorded": decision_recorded, "formal_decision_id": decision_id}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
