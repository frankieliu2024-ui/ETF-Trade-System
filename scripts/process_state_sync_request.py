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
    """Return the latest canonical post-close review as a display projection."""
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


def _settlement_obligation_key(item: dict) -> str:
    economic_fields = (
        "obligation_type", "security_code", "quantity", "subscription_price", "required_cash"
    )

    def normalized_value(key: str) -> str:
        value = item.get(key)
        if key in {"quantity", "subscription_price", "required_cash"}:
            number = safe_float(value)
            if number is not None:
                return format(number, ".12g")
        return str(value or "").strip()

    economic_values = [normalized_value(key) for key in economic_fields]
    if all(economic_values):
        return "economic:" + "|".join(economic_values)
    explicit = str(item.get("obligation_id") or item.get("idempotency_key") or "").strip()
    if explicit:
        return "explicit:" + explicit
    return "unidentified:" + "|".join(economic_values)


def _settlement_status(item: dict) -> str:
    return str(item.get("status") or "").upper().replace("-", "_").replace(" ", "_")


def _canonicalize_settlement_account(account: dict) -> dict:
    obligations = account.get("settlement_obligations")
    if obligations is None:
        obligations = []
    if not isinstance(obligations, list):
        raise ValueError("settlement_obligations must be a list")
    normalized = []
    for raw in obligations:
        if not isinstance(raw, dict):
            continue
        item = json.loads(json.dumps(raw))
        item["status"] = _settlement_status(item) or "PENDING"
        item.setdefault("pit_timestamp", item.get("updated_at") or account.get("updated_at") or "")
        item.setdefault("source", account.get("source") or "ACCOUNT_FACT")
        item.setdefault("obligation_type", "SETTLEMENT")
        normalized.append(item)
    account["settlement_obligations"] = normalized
    active = {"PENDING", "PENDING_PAYMENT", "DEADLINE_PASSED_UNCONFIRMED", "UNCONFIRMED_DEADLINE_PASSED"}
    reserved = round(sum(safe_float(x.get("required_cash")) or 0.0 for x in normalized if _settlement_status(x) in active), 2)
    cash = safe_float(account.get("cash"))
    account["reserved_cash_for_settlement"] = reserved
    account["deployable_cash"] = round(max((cash or 0.0) - reserved, 0.0), 2) if cash is not None else None
    account["settlement_cash_shortfall"] = round(max(reserved - (cash or 0.0), 0.0), 2) if cash is not None else None
    if reserved and cash is not None and cash < reserved:
        account["settlement_constraint_status"] = "INSUFFICIENT_CASH"
    elif any(_settlement_status(x) in {"DEADLINE_PASSED_UNCONFIRMED", "UNCONFIRMED_DEADLINE_PASSED"} for x in normalized):
        account["settlement_constraint_status"] = "DEADLINE_PASSED_UNCONFIRMED"
    elif reserved:
        account["settlement_constraint_status"] = "RESERVED"
    else:
        account["settlement_constraint_status"] = "NONE"
    return account


def _merge_settlement_obligations(prior: dict, supplied: dict) -> list[dict]:
    prior_items = prior.get("settlement_obligations") or []
    incoming_items = supplied.get("settlement_obligations")
    if incoming_items is None:
        return json.loads(json.dumps(prior_items))
    by_key = {_settlement_obligation_key(x): json.loads(json.dumps(x)) for x in prior_items if isinstance(x, dict)}
    rank = {"PENDING": 1, "PENDING_PAYMENT": 1, "DEADLINE_PASSED_UNCONFIRMED": 2, "UNCONFIRMED_DEADLINE_PASSED": 2, "SETTLED": 3, "CANCELLED": 3, "RELEASED": 3}
    for item in incoming_items:
        if not isinstance(item, dict):
            continue
        key = _settlement_obligation_key(item)
        old = by_key.get(key)
        if old and rank.get(_settlement_status(old), 0) > rank.get(_settlement_status(item), 0):
            continue
        merged = json.loads(json.dumps(old or {}))
        for field, value in item.items():
            if value is not None:
                merged[field] = json.loads(json.dumps(value))
        by_key[key] = merged
    return list(by_key.values())


def _annotate_confirmed_ipo_origins(account: dict) -> None:
    confirmed = {str(x.get("security_code") or "") for x in (account.get("settlement_obligations") or []) if _settlement_status(x) in {"SETTLED", "CANCELLED", "RELEASED"} and str(x.get("obligation_type") or "").upper() == "IPO_ALLOTMENT_PAYMENT"}
    for position in account.get("positions") or []:
        if str(position.get("code") or "") in confirmed:
            position["origin"] = "IPO_ALLOTMENT_ORIGIN"
            position["origin_fact"] = "confirmed_settlement_obligation"


def latest_formal_risk_fact() -> dict:
    review_dir = ROOT / "events" / "reviews"
    candidates = []
    if review_dir.exists():
        for path in review_dir.glob("*.json"):
            try:
                event = load_json(path)
            except Exception:
                continue
            review = event.get("review") or event.get("formal_review") or {}
            fact = review.get("etf_strategy_known_net") or {}
            risk = safe_float(fact.get("etf_strategy_risk_rate_pct"))
            equity = safe_float(fact.get("known_net_strategy_equity"))
            pnl = safe_float(fact.get("known_net_cumulative_pnl"))
            updated = parse_time(event.get("updated_at_beijing") or event.get("account_updated_at"))
            if risk is None or equity is None or updated is None:
                continue
            calculated = (pnl / 200000.0 * 100.0) if pnl is not None else ((equity / 200000.0 - 1.0) * 100.0)
            if abs(calculated - risk) > 0.03:
                continue
            candidates.append((updated, {
                "risk_rate_pct": risk,
                "strategy_equity_known_net": equity,
                "cumulative_pnl_known_net": pnl,
                "market_date": event.get("market_date") or review.get("market_date"),
                "updated_at_beijing": updated.isoformat(timespec="seconds"),
                "source": str(path.relative_to(ROOT)).replace("\\", "/"),
            }))
    return max(candidates, key=lambda x: x[0])[1] if candidates else {}


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


def sync_current_account_mirror(root: Path, account: dict) -> None:
    current_path = root / "data" / "state" / "CURRENT.json"
    current = load_json(current_path) if current_path.exists() else {}
    current["account_fact"] = {
        key: account.get(key, "")
        for key in (
            "status", "updated_at", "source", "cash",
            "reserved_cash_for_settlement", "deployable_cash",
            "settlement_constraint_status",
        )
    }
    current["settlement_obligations"] = account.get("settlement_obligations") or []
    current["reserved_cash_for_settlement"] = account.get("reserved_cash_for_settlement", 0)
    current["deployable_cash"] = account.get("deployable_cash")
    current["needs_account_update"] = account.get("status") != "VALID"
    atomic_json_write(current_path, current)


def build_dashboard_block(account: dict, decision: dict | None, request: dict) -> str:
    positions = account.get("positions") or []
    membership = active_account_asset_codes(ROOT, account)
    etfs = [p for p in positions if normalize_code(p.get("code")) in membership["etf"]]
    stocks = [p for p in positions if normalize_code(p.get("code")) in membership["stocks"]]
    etf_pnl = sum(position_metric(p, "pnl", "holding_pnl") for p in etfs)
    formal_risk = build_etf_strategy_risk_metrics(ROOT)
    risk_rate = safe_float(formal_risk.get("etf_strategy_risk_pct"))
    formal_risk_equity = safe_float(formal_risk.get("strategy_equity_known_net"))
    if risk_rate is None:
        risk_rate = etf_pnl / 200000.0 * 100.0
    total_asset = float(account.get("total_asset") or 0)
    exposure = (float(account.get("stock_market_value") or 0) / total_asset * 100.0) if total_asset else 0.0
    scenario = request.get("interaction_scenario") or "UNSPECIFIED"
    lines = ["## 云端实时状态（自动同步）", "", f"> 更新时间：{account.get('updated_at','')}  ", f"> 来源：{account.get('source','')}  ", f"> 场景：{scenario}  ", "> 本区块只同步已确认账户事实与ChatGPT已形成的正式决策；自动程序不得自行推导交易权限或下单。", ""]
    lines += ["|项目|最新事实|", "|-|-|", f"|总资产|{money(account.get('total_asset'))}|", f"|股票市值|{money(account.get('stock_market_value'))}|", f"|可用资金|{money(account.get('cash'))}|", f"|预留结算资金|{money(account.get('reserved_cash_for_settlement'))}|", f"|可部署现金|{money(account.get('deployable_cash'))}|", f"|账户总风险暴露率|约{exposure:.2f}%|", f"|ETF策略风险率|约{risk_rate:.4f}%（Gross）|" if risk_rate is not None else "|ETF策略风险率|未提供|", f"|ETF策略权益|{money(formal_risk_equity)}|" if formal_risk_equity is not None else "|ETF策略权益|未提供|"]
    lines += ["", "### 当前持仓事实", ""]
    for p in positions:
        lines.append(f"- {display_name(p)}：{p.get('quantity', 0)}；成本{p.get('cost_price', p.get('cost', ''))}；最新{p.get('last_price', p.get('price', ''))}。")
    if decision:
        lines += ["", "### 最近正式决策", "", f"- 风险许可：{decision.get('risk_permission','未提供')}", f"- 主候选：{decision.get('main_candidate','未提供')}", f"- 生命周期：{decision.get('lifecycle','未提供')}", f"- 金额与动作：{decision.get('amount_action', decision.get('action','未提供'))}"]
    return "\n".join(lines) + "\n"


def build_comparison_snapshot(snapshot: dict) -> dict:
    rows = snapshot.get("rows") or []
    items = []
    for row in rows:
        if row.get("quality_status") != "PASS":
            continue
        items.append({
            "symbol": row.get("symbol"),
            "name": row.get("name") or row.get("provider_name"),
            "close": row.get("close"),
            "change_pct": row.get("change_pct"),
            "as_of_beijing": row.get("as_of_beijing") or row.get("provider_as_of_beijing"),
        })
    return {
        "as_of_beijing": snapshot.get("captured_at_beijing") or snapshot.get("captured_at"),
        "items": items,
        "interpretation_rule": "只保留决策时点可见的可比行情事实，不使用未来数据回填。",
    }


def build_market_structure_projection(snapshot: dict) -> dict:
    context_path = ROOT / "data/state/market_structure_context.json"
    context = load_json(context_path) if context_path.exists() else {}
    projection_items = []
    delta_path = ROOT / "data/state/market_delta.json"
    delta = load_json(delta_path) if delta_path.exists() else {}
    delta_time = parse_time(delta.get("as_of_beijing"))
    cutoff = parse_time(snapshot.get("captured_at_beijing") or snapshot.get("captured_at"))
    delta_time_valid = not delta_time or not cutoff or delta_time <= cutoff
    delta_by_code = {str(x.get("symbol") or x.get("code") or ""): x for x in (delta.get("items") or [])}
    items = context.get("items") or []
    names = []
    for item in items:
        code = str(item.get("code") or item.get("symbol") or "")
        names.append(code)
        status = str(item.get("status") or "UNKNOWN")
        historical = item.get("historical_trend") or {}
        participation = item.get("participation") or {}
        turnover = item.get("turnover") or {}
        projection_items.append({
            "code": code,
            "status": status,
            "historical_trend": {
                "trend_state": historical.get("trend_state"),
                "trend_detail": historical.get("trend_detail"),
            },
            "participation": {
                "status": participation.get("status") or turnover.get("status"),
                "completed_bar_date": participation.get("completed_bar_date"),
                "participation_ratio_vs_prior_20d": participation.get("participation_ratio_vs_prior_20d") if participation.get("participation_ratio_vs_prior_20d") is not None else turnover.get("time_normalized_amount_pace_ratio"),
                "acceptance_behavior": participation.get("acceptance_behavior") or turnover.get("acceptance_behavior"),
                "enhancement_active": participation.get("enhancement_active"),
            },
            "event_delta": delta_by_code.get(code, {}) if status == "READY" and delta_time_valid else {},
        })
    statuses = {item["status"] for item in projection_items}
    return {
        "status": "READY" if statuses and statuses == {"READY"} else "PARTIAL_FAIL_SAFE",
        "as_of_beijing": context.get("as_of_beijing"),
        "source": "data/state/market_structure_context.json",
        "pit_cutoff": snapshot.get("captured_at_beijing") or snapshot.get("captured_at"),
        "identity_set": list(names),
        "items": projection_items,
        "consumer_contract": {
            "buy_candidate": {"consumer": "existing formal decision and research evidence path", "use": "candidate comparison evidence only", "can_generate_action_independently": False},
            "held_position_sell": {"consumer": "existing formal decision and research evidence path", "use": "held-position evidence and opportunity-cost comparison only", "can_generate_action_independently": False},
        },
        "decision_boundary": "STATE/PERSISTENCE只提供可审计证据；不得独立生成Trial、Confirm、降低风险、退出、金额或订单动作。缺失、过期、未来或不可验证事实必须显式降级，禁止静默沿用旧状态。",
        "failure_contract": "context或item缺失、context/item超出PIT cutoff、或delta超出PIT cutoff时，保留显式状态并省略不可验证字段。",
    }


def resolve_hypothesis_id(decision: dict, code: str, market_date: str, decision_id: str) -> tuple[str, str]:
    explicit = str(decision.get("hypothesis_id") or "").strip()
    if explicit:
        return explicit, "EXPLICIT"
    lifecycle = str(decision.get("lifecycle") or "")
    if code and "Trial" in lifecycle:
        return f"HYP_{code}_{market_date.replace('-', '')}_{decision_id[-8:]}", "NEW_TRIAL"
    prior_dir = ROOT / "events/decisions"
    if code and prior_dir.exists():
        priors = []
        for path in prior_dir.glob("*.json"):
            try:
                obj = load_json(path)
            except Exception:
                continue
            if str(obj.get("candidate_code") or "") == code and obj.get("hypothesis_id"):
                priors.append(obj)
        if priors:
            priors.sort(key=lambda x: str(x.get("decision_time_beijing") or ""))
            latest = priors[-1]
            if not latest.get("hypothesis_closed"):
                return str(latest["hypothesis_id"]), "CARRY_FORWARD_PRIOR"
            return "", "PRIOR_HYPOTHESIS_CLOSED"
    return "", "UNRESOLVED"


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
    request_id = str(request.get("request_id") or "")
    fingerprint = hashlib.sha256(json.dumps({"request_id": request_id, "market_date": market_date, "formal_decision": decision}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    decision_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(decision.get("decision_id") or request_id or f"{market_date}_{fingerprint[:12]}"))
    decision_time = str(decision.get("data_as_of_beijing") or "") or datetime.now(SHANGHAI).isoformat(timespec="seconds")
    availability_time = str(request.get("persistence_available_at_beijing") or request.get("decision_persisted_at_beijing") or decision.get("issued_at_beijing") or decision.get("decision_effective_at_beijing") or request.get("requested_at_beijing") or "")
    consumed_snapshot = str(request.get("consumed_snapshot") or request.get("consumed_snapshot_path") or decision.get("consumed_snapshot") or decision.get("consumed_snapshot_path") or "")
    external_evidence = None
    request_type = str(request.get("request_type") or "").upper()
    external_path = str(request.get("consumed_external_market_evidence") or "").strip()
    if request_type == "EMERGENCY_EXTERNAL_MARKET_EVIDENCE":
        if consumed_snapshot:
            raise ValueError("external evidence and consumed_snapshot are mutually exclusive")
        external_evidence = validate_external_market_evidence(request, ROOT, decision_time=decision_time, availability_time=availability_time, ingress_path=str(request.get("_ingress_path") or ""))
        snapshot_rel = external_evidence["path"]
        snapshot = {"market_date": external_evidence["market_date"], "captured_at_beijing": external_evidence["retrieved_at_beijing"], "quality_status": "PASS", "rows": external_evidence["rows"]}
        pit_status = "EXTERNAL_MARKET_EVIDENCE_VALIDATED"
    elif external_path:
        raise ValueError("external evidence path requires EMERGENCY_EXTERNAL_MARKET_EVIDENCE request_type")
    else:
        snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(market_date, decision_time, availability_time=availability_time, consumed_snapshot=consumed_snapshot)
    supplied_price = safe_float(decision.get("price_at_decision"))
    supplied_as_of = str(decision.get("price_as_of_beijing") or "")
    supplied_time = parse_time(supplied_as_of)
    cutoff = parse_time(decision_time)
    supplied_point_in_time = supplied_price is not None and supplied_time is not None and cutoff is not None and supplied_time <= cutoff
    price_at_decision = supplied_price if supplied_point_in_time else None
    price_as_of = supplied_as_of if supplied_point_in_time else ""
    price_source = "FORMAL_DECISION_SUPPLIED_POINT_IN_TIME" if supplied_point_in_time else ""
    if price_at_decision is None and code and snapshot:
        row = next((x for x in (snapshot.get("rows") or []) if str(x.get("symbol")) == code and x.get("quality_status") == "PASS"), None)
        if row:
            price_at_decision = row.get("close")
            price_as_of = str(row.get("as_of_beijing") or "")
            price_source = "POINT_IN_TIME_SNAPSHOT"
    if external_evidence:
        evidence_row = next((x for x in external_evidence["rows"] if str(x.get("symbol") or "").split(".")[0] == code.split(".")[0]), None)
        if evidence_row is None:
            raise ValueError("external evidence does not contain formal decision candidate")
        evidence_price = safe_float(evidence_row.get("close"))
        evidence_as_of = str(evidence_row.get("provider_as_of_beijing") or "")
        if supplied_price is not None and (evidence_price is None or abs(supplied_price - evidence_price) > 1e-12):
            raise ValueError("supplied price does not match external evidence")
        if supplied_as_of and supplied_as_of != evidence_as_of:
            raise ValueError("supplied price time does not match external evidence")
        price_at_decision = evidence_price
        price_as_of = evidence_as_of
        price_source = "EMERGENCY_EXTERNAL_MARKET_EVIDENCE"
    elif supplied_price is not None and not supplied_point_in_time and not price_source:
        pit_status = "SUPPLIED_PRICE_REJECTED_NO_VERIFIABLE_POINT_IN_TIME"
    hypothesis_id, hypothesis_link_status = resolve_hypothesis_id(decision, code, market_date, decision_id)
    lifecycle = str(decision.get("lifecycle") or "")
    hypothesis_closed = "退出" in lifecycle or str(decision.get("hypothesis_status") or "").upper() == "CLOSED"
    comparison = build_comparison_snapshot(snapshot) if snapshot else {"items": [], "interpretation_rule": "决策时点无可用历史快照，不使用未来数据补齐。"}
    interactive_consumed = bool(request.get("interactive_result_consumed"))
    interactive_delivery = {
        "mode": str(request.get("interaction_channel") or ("LIVE_CHAT" if interactive_consumed else "UNSPECIFIED")),
        "status": "CONSUMED" if interactive_consumed else "UNSEEN",
        "request_id": request_id,
        "consumed_at_beijing": str(request.get("interactive_consumed_at_beijing") or request.get("requested_at_beijing") or "") if interactive_consumed else "",
    }
    event = {
        "event_type": "FORMAL_DECISION",
        "decision_id": decision_id,
        "fingerprint": fingerprint,
        "market_date": market_date,
        "decision_time_beijing": decision_time,
        "decision_effective_at_beijing": str(decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing") or ""),
        "decision_effective_ordering": str(decision.get("decision_effective_ordering") or ""),
        "timing_quality": str(decision.get("timing_quality") or ""),
        "timing_provenance": str(decision.get("timing_provenance") or ""),
        "interaction_scenario": request.get("interaction_scenario"),
        "interactive_delivery": interactive_delivery,
        "candidate_code": code,
        "candidate_name": name,
        "hypothesis_id": hypothesis_id,
        "hypothesis_link_status": hypothesis_link_status,
        "hypothesis_closed": hypothesis_closed,
        "price_at_decision": price_at_decision,
        "price_as_of_beijing": price_as_of,
        "price_source_snapshot": snapshot_rel,
        "price_source": price_source,
        "point_in_time_status": pit_status,
        "comparison_snapshot": comparison,
        "formal_decision": decision,
        "managed_position_sell_review": managed_projection,
        "read_only_research_event": True,
        "decision_boundary": "只保存ChatGPT已经形成的正式决策和决策时点可见证据。禁止使用决策时点之后的行情回填价格或比较快照；研究留痕用于验证候选选择、假设生命周期、判断与执行质量，不自行推导交易权限。",
        "market_evidence_type": "EMERGENCY_EXTERNAL_MARKET_EVIDENCE" if external_evidence else "CANONICAL_SNAPSHOT",
        "external_evidence_path": external_evidence["path"] if external_evidence else "",
        "external_evidence_id": external_evidence["evidence_id"] if external_evidence else "",
        "external_evidence_validation": external_evidence["validation_status"] if external_evidence else "",
        "external_evidence_scope": external_evidence.get("evidence_scope", "") if external_evidence else "",
        "decision_evidence_eligibility": external_evidence.get("decision_evidence_eligibility", "") if external_evidence else "",
        "execution_price_eligibility": external_evidence.get("execution_price_eligibility", "") if external_evidence else "",
        "execution_revalidation_required": bool(external_evidence.get("execution_revalidation_required")) if external_evidence else False,
        "standalone_action_allowed": external_evidence.get("standalone_action_allowed") if external_evidence else None,
        "execution_boundary": "人工执行前必须刷新当前最新行情并重新确认；条件失效时不得执行原计划" if external_evidence and external_evidence.get("execution_price_eligibility") != "EXECUTION_PRICE_ELIGIBLE" else "",
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


def validate_formal_decision_contract(decision: dict) -> str:
    opportunity_status = str(decision.get("opportunity_status") or "").strip()
    if opportunity_status not in FORMAL_OPPORTUNITY_STATUSES:
        return "opportunity_status must be one of: " + ", ".join(sorted(FORMAL_OPPORTUNITY_STATUSES))
    risk = decision.get("risk_permission")
    if risk is not None and str(risk).strip() not in {"禁止新增", "允许Trial", "允许Confirm"}:
        return "risk_permission is not a registered formal value"
    lifecycle_error = _validate_formal_lifecycle(decision.get("lifecycle"))
    if lifecycle_error:
        return lifecycle_error
    lifecycle_error = validate_current_lifecycle_contract(decision.get("lifecycle"), _load_account_for_lifecycle_validation())
    if lifecycle_error:
        return lifecycle_error
    return ""


def _validate_formal_lifecycle(value: object, object_name: str = "lifecycle") -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        for security, action in value.items():
            if not str(security).strip():
                return f"{object_name} contains an empty object key"
            error = _validate_formal_lifecycle(action, f"{object_name}[{security}]")
            if error:
                return error
        return ""
    if not isinstance(value, str):
        return f"{object_name} must be text or an object-to-lifecycle mapping"
    text = value.strip()
    if not text:
        return ""
    clauses = [part.strip() for part in text.replace(";", "；").split("；") if part.strip()]
    current_seen = False
    for clause in clauses:
        historical_exit = "已退出" in clause
        if "持有并" in clause or "持有且" in clause:
            return f"{object_name} clause must state the current holding action explicitly: {clause}"
        current_actions = ("持有管理", "持仓管理", "降低风险", "退出")
        has_current_action = any(action in clause and not (action == "退出" and historical_exit) for action in current_actions)
        legacy_hold = "持有" in clause and not has_current_action and "持有并" not in clause and "持有且" not in clause
        if not has_current_action and current_seen and (historical_exit or any(term in clause for term in ("Trial", "Confirm", "观察", "已关闭", "继续"))):
            continue
        if not has_current_action and not legacy_hold:
            return f"{object_name} clause has no registered lifecycle action: {clause}"
        current_seen = True
    return ""


def _load_account_for_lifecycle_validation() -> dict:
    account_path = ROOT / "data" / "state" / "account_fact.json"
    return load_json(account_path) if account_path.exists() else {}


def _lifecycle_object_code(security: object) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(security or ""))
    return normalize_code(match.group(1)) if match else ""


def _is_historical_lifecycle_explanation(text: str) -> bool:
    return any(marker in text for marker in ("历史", "曾", "来源", "解释", "原", "previous", "historical"))


def _managed_position_code(key: object, managed: list[dict]) -> str:
    text = str(key or "")
    for item in managed:
        if item["code"] in text or item["name"] in text:
            return item["code"]
    return ""


def validate_current_lifecycle_contract(lifecycle: object, account: dict) -> str:
    return ""


def validate_managed_position_lifecycle(lifecycle: object, account: dict, field_name: str) -> str:
    return ""


def validate_managed_position_review_contract(reviews: object, account: dict, field_name: str) -> str:
    return ""


def select_point_in_time_snapshot(market_date: str, decision_time: str, *, availability_time: str = "", consumed_snapshot: str = "") -> tuple[str, dict, str]:
    snapshot_dir = ROOT / "data/market/snapshots"
    if consumed_snapshot:
        path = ROOT / consumed_snapshot
        if path.exists():
            return consumed_snapshot, load_json(path), "EXPLICIT_CONSUMED_SNAPSHOT"
    candidates = []
    cutoff = parse_time(decision_time)
    for path in snapshot_dir.glob(f"{market_date}_*.json") if snapshot_dir.exists() else []:
        try:
            snapshot = load_json(path)
        except Exception:
            continue
        stamp = parse_time(snapshot.get("captured_at_beijing") or snapshot.get("captured_at"))
        if stamp and cutoff and stamp <= cutoff:
            candidates.append((stamp, path, snapshot))
    if not candidates:
        return "", {}, "NO_ELIGIBLE_POINT_IN_TIME_SNAPSHOT"
    candidates.sort(key=lambda x: x[0])
    _, path, snapshot = candidates[-1]
    return str(path.relative_to(ROOT)).replace("\\", "/"), snapshot, "POINT_IN_TIME_SNAPSHOT_SELECTED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    args = parser.parse_args()
    request_path = Path(args.request)
    if not request_path.is_absolute():
        request_path = ROOT / request_path
    request = load_json(request_path)
    request["_ingress_path"] = str(request_path.relative_to(ROOT)).replace("\\", "/") if request_path.is_relative_to(ROOT) else str(request_path)
    recorded, decision_id = record_formal_decision(request)
    if recorded:
        print(json.dumps({"formal_decision": "RECORDED", "decision_id": decision_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
