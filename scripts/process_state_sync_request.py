Warning: truncated output (original token count: 59230)
Total output lines: 4162

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
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
from sync_formal_files import sync_formal_files, latest_canonical_formal_decision, format_position_pnl
from observation_etf_management import persist_monitor_universe, validate_observation_management
try:
    from business_decision_source import (
        BUSINESS_DECISION_SOURCE,
        build_formal_completion_from_source,
        project_decision_response,
        source_fingerprint,
        validate_formal_presentation_provenance,
        emit_formal_replay_forensic_fingerprint,
        reset_formal_replay_forensic_trace,
        validate_source,
    )
except ModuleNotFoundError:
    from scripts.business_decision_source import (
        BUSINESS_DECISION_SOURCE,
        build_formal_completion_from_source,
        project_decision_response,
        source_fingerprint,
        validate_formal_presentation_provenance,
        emit_formal_replay_forensic_fingerprint,
        reset_formal_replay_forensic_trace,
        validate_source,
    )
try:
    from user_visible_presentation import build_formal_decision_presentation_binding
except ModuleNotFoundError:
    from scripts.user_visible_presentation import build_formal_decision_presentation_binding
from formal_file_mutation_gateway import (
    append_managed_line,
    replace_formal_block,
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
    from build_post_market_review import build_close_data_contract
except ModuleNotFoundError:
    from scripts.build_post_market_review import build_close_data_contract
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

def should_project_formal_decision_current_state(request: dict, decision_id: str, root: Path = ROOT) -> bool:
    """Historical replay may project current state only when it is the unique latest decision."""
    if request.get("_historical_replay") is not True:
        return True
    decision_id = str(decision_id or "").strip()
    decision_dir = root / "events" / "decisions"
    if not decision_id or not decision_dir.is_dir():
        return False

    records = []
    for path in decision_dir.glob("*.json"):
        try:
            event = load_json(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        if str(event.get("event_type") or "").upper() != "FORMAL_DECISION":
            continue
        decision = event.get("formal_decision") or event.get("decision") or {}
        if not isinstance(decision, dict):
            return False
        event_id = str(event.get("decision_id") or decision.get("decision_id") or "").strip()
        stamp = (
            event.get("decision_time_beijing")
            or event.get("decision_effective_at_beijing")
            or decision.get("decision_time")
            or decision.get("decision_effective_at_beijing")
            or event.get("recorded_at_beijing")
        )
        parsed = parse_time(stamp)
        if not event_id or parsed is None:
            return False
        records.append((parsed, event_id))

    matching = [stamp for stamp, event_id in records if event_id == decision_id]
    if len(matching) != 1:
        return False
    target_time = matching[0]
    # Equal-time decisions are ambiguous unless they are the same unique event.
    return not any(
        event_id != decision_id and stamp >= target_time
        for stamp, event_id in records
    )


def project_current_formal_action(
    account: dict,
    request: dict,
    decision_id: str,
    root: Path = ROOT,
) -> tuple[bool, bool]:
    """Project a current action without letting stale historical replay roll state back."""
    if not should_project_formal_decision_current_state(request, decision_id, root):
        return False, False
    decision = request.get("formal_decision") or {}
    prior_action = account.get("formal_action") or {}
    same_executed_decision = (
        str(prior_action.get("decision_id") or "") == decision_id
        and str(prior_action.get("execution_status") or "").upper() == "EXECUTED"
    )
    if same_executed_decision:
        return True, False
    next_action = {
        "action": decision.get("action") or decision.get("amount_action") or "",
        "quantity": decision.get("quantity"),
        "decision_id": decision_id,
        "decision_time": decision.get("decision_time") or decision.get("data_as_of_beijing") or datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "source": "CHATGPT_FORMAL_DECISION",
        "lifecycle": decision.get("lifecycle"),
        "applicable_object": decision.get("candidate_code") or decision.get("code") or "",
        "validity": "ACTIVE",
        "execution_status": "PENDING",
    }
    if next_action == prior_action:
        return True, False
    account["formal_action"] = next_action
    return True, True



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
    # Economic identity is authoritative only when every field required to
    # distinguish an obligation is present. Partial records must retain an
    # explicit ingress identity instead of being merged by a weak key.
    if all(economic_values):
        return "economic:" + "|".join(economic_values)

    explicit = str(item.get("obligation_id") or item.get("idempotency_key") or "").strip()
    if explicit:
        return "explicit:" + explicit

    # Keep incomplete records addressable without colliding with a complete
    # economic obligation.
    return "unidentified:" + "|".join(economic_values)


def _settlement_status(item: dict) -> str:
    return str(item.get("status") or "").upper().replace("-", "_").replace(" ", "_")


def _canonicalize_settlement_account(account: dict) -> dict:
    """Derive settlement cash from confirmed obligations; request derived values are hints only."""
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
    """Keep CURRENT's account summary aligned with canonical account_fact."""
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
    # Keep the top-level CURRENT account reference authoritative after every
    # canonical account ingress.  The nested mirror alone is insufficient for
    # consumers that read the compact CURRENT projection.
    current["account_fact_path"] = "data/state/account_fact.json"
    current["account_fact_updated_at"] = account.get("updated_at", "")
    current["account_fact_source"] = account.get("source", "")
    current["account_fact_status"] = account.get("status", "")
    current["account_fact_reference_synced_at"] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    atomic_json_write(current_path, current)



def build_dashboard_block(account: dict, decision: dict | None, request: dict) -> str:
    positions = account.get("positions") or []
    membership = active_account_asset_codes(ROOT, account)
    etfs = [p for p in positions if normalize_code(p.get("code")) in membership["etf"]]
    stocks = [p for p in positions if normalize_code(p.get("code")) in membership["stocks"]]
    etf_pnl = sum(position_metric(p, "pnl", "holding_pnl") for p in etfs)
    # Current formal risk is projected from the shared Gross canonical owner.
    # Review Known-net facts remain historical PIT compatibility evidence only.
    formal_risk = build_etf_strategy_risk_metrics(ROOT)
    risk_rate = safe_float(formal_risk.get("etf_strategy_risk_pct"))
    formal_risk_equity = safe_float(formal_risk.get("strategy_equity_known_net"))
    if risk_rate is None:
        risk_rate = etf_pnl / 200000.0 * 100.0
    total_asset = float(account.get("total_asset") or 0)
    exposure = (float(account.get("stock_market_value") or 0) / total_asset * 100.0) if total_asset else 0.0
    scenario = request.get("interaction_scenario") or "UNSPECIFIED"
    lines = ["## 云端实时状态（自动同步）", "", f"> 更新时间：{account.get('updated_at','')}  ", f"> 来源：{account.get('source','')}  ", f"> 场景：{scenario}  ", "> 本区块只同步已确认账户事实与ChatGPT已形成的正式决策；自动程序不得自行推导交易权限或下单。", "", "|项目|最新事实|", "|-|-|", f"|总资产|{money(account.get('total_asset'))}|", f"|股票市值|{money(account.get('stock_market_value'))}|", f"|可用资金|{money(account.get('cash'))}|", f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|", f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|", f"|账户总风险暴露率|约{exposure:.2f}%|", f"|ETF持仓浮动盈亏|{money(etf_pnl)}|", *(([f"|ETF策略Gross权益|{money(formal_risk_equity)}|"] if formal_risk_equity is not None else [])), f"|ETF策略当前正式风险率|约{risk_rate:.2f}%（Gross canonical；Known-net仅作历史兼容）|", "", "### 当前持仓事实", "", "|标的|数量|成本|现价|市值|浮动盈亏|", "|-|-:|-:|-:|-:|-:|"]
    for p in positions:
        lines.append(f"|{display_name(p)}|{int(p.get('quantity') or 0):,}|{float(p.get('cost') or 0):.3f}|{position_metric(p, 'current_price', 'last_price'):.3f}|{money(p.get('market_value'))}|{format_position_pnl(p)}|")
    universe = load_json(ROOT / "config/market/etf_monitor_universe.json")
    names = {str(item.get("code")): str(item.get("name") or item.get("code")) for item in (universe.get("objects") or []) if item.get("code")}
    observed = [f"{name}（{code}）" for code, name in names.items() if code not in membership["etf"]]
    lines += ["", f"持仓ETF：{'、'.join(display_name(p) for p in etfs) or '无'}。", f"账户个股：{'、'.join(display_name(p) for p in stocks) or '无'}。", f"观察ETF：{'、'.join(observed) or '无'}。"]
    if decision:
        title = "最近一次正式决策"
        lifecycle_lines = _managed_lifecycle_lines(decision.get("lifecycle"), account)
        lifecycle_block = ["- 生命周期："] + lifecycle_lines if lifecycle_lines else [f"- 生命周期：{decision.get('lifecycle','未提供')}"]
        lines += ["", f"### {title}", "", f"- 风险许可：{decision.get('risk_permission','未提供')}", *lifecycle_block, f"- 唯一主候选：{decision.get('main_candidate','无新的主候选。')}", f"- 金额与动作：{decision.get('amount_action','未提供')}", f"- 最大风险或0元主因：{decision.get('decisive_reason','未提供')}", f"- 决策数据时点：{decision.get('data_as_of_beijing','未提供')}"]
    else:
        lines += ["", "最近一次正式决策未随本次同步请求提供；脚本不自行推断，保留人工/ChatGPT正式决议。"]
    lines += ["", f"同步请求：`{request.get('request_id','')}`。"]
    lifecycle = build_lifecycle_projection(ROOT)
    lines += ["", "### 当前交易生命周期", ""]
    if lifecycle.get("active_lifecycles"):
        for item in lifecycle["active_lifecycles"]:
            due = "；今日必须形成T+3正式决议" if item.get("decision_due") else ""
            lines.append(f"- {item.get('security_name') or item.get('security_code')}（{item.get('security_code')}）：{item.get('lifecycle_status')}，起始交易日{item.get('start_market_date')}，当前T+{item.get('current_t_plus')}，下一节点{item.get('next_lifecycle_node')}{due}。")
    else:
        lines.append("- 当前没有可由正式决策与真实成交事实恢复的未关闭Trial生命周期。")
    return "\n".join(lines)


def _snapshot_fact_times(snapshot: dict) -> list[datetime]:
    """Return all provider/row market-fact times carried by a snapshot."""
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

def _manual_completion_parent_request(request: dict) -> dict:
    """Load and validate the durable parent request for manual formal completion."""
    parent_id = str(request.get("parent_request_id") or "").strip()
    if not parent_id or any(part in parent_id for part in ("/", "\\", "..")):
        raise ValueError("manual formal completion requires a safe parent request identity")
    parent_path = ROOT / "requests" / "live_snapshot" / f"{parent_id}.json"
    if not parent_path.exists():
        raise ValueError("manual formal completion parent request is not durable")
    parent = load_json(parent_path)
    if str(parent.get("request_id") or "").strip() != parent_id:
        raise ValueError("manual formal completion parent request identity mismatch")
    intent = str(parent.get("intent") or parent.get("query_intent") or "").strip().upper()
    if intent not in {"EXPLICIT_LATEST", "FORMAL_INTRADAY_ANALYSIS"}:
        raise ValueError("manual formal completion parent is not a formal market request")
    if not (
        parent.get("require_post_request_snapshot") is True
        or parent.get("require_post_request_current") is True
    ):
        raise ValueError("manual formal completion parent does not require post-request market facts")
    if parse_time(parent.get("requested_at_beijing") or parent.get("request_time_beijing") or parent.get("requested_at_utc")) is None:
        raise ValueError("manual formal completion parent request time is invalid")
    return parent


def _manual_completion_snapshot_is_post_request(parent: dict, snapshot: dict) -> bool:
    """Apply the same effective-time lower bound used by decision freshness."""
    requested = parse_time(
        parent.get("requested_at_beijing")
        or parent.get("request_time_beijing")
        or parent.get("requested_at_utc")
    )
    captured = parse_time(snapshot.get("captured_at_beijing") or snapshot.get("captured_at"))
    provider = parse_time(snapshot.get("provider_as_of") or snapshot.get("provider_as_of_beijing"))
    effective = min([value for value in (captured, provider) if value is not None], default=None)
    return bool(requested and effective and effective >= requested)


def _manual_completion_envelope_pit_ready(request: dict, parent: dict, parent_request_id: str) -> bool:
    """Validate immutable request-bound PIT closure captured when the formal conclusion was built."""
    closure = request.get("request_bound_pit_closure") or {}
    requested = str(parent.get("requested_at_beijing") or parent.get("request_time_beijing") or "").strip()
    return bool(
        isinstance(closure, dict)
        and str(closure.get("parent_request_id") or "").strip() == str(parent_request_id or "").strip()
        and str(closure.get("status") or "").upper() == "READY"
        and closure.get("request_scoped_pit_resolved") is True
        and str(closure.get("decision_request_time") or "").strip() == requested
        and closure.get("refresh_mode") in {"QUERY_TIME_IMMEDIATE_REFRESH", "REUSED_REQUEST_BOUND_FACTS"}
        and closure.get("freshness_status") in {"DIRECT", "REUSED", "READY"}
    )


def _manual_completion_request_bound_pit_ready(query_context: dict, parent_request_id: str) -> bool:
    """Accept the existing request-bound query-time PIT closure without inventing a new snapshot.

    A prior canonical A-share snapshot may remain the legal comparison/PIT base
    outside the A-share session while query-time providers resolve the new
    request's current market facts.  Persistence may use that combination only
    when the existing decision packet is bound to the same parent request and
    its action gate explicitly says the request-scoped PIT is resolved.
    """
    packet = query_context.get("decision_fact_pack") or {}
    trigger = packet.get("trigger") or {}
    action = packet.get("formal_action_readiness") or {}
    freshness = query_context.get("freshness_assurance") or {}
    return bool(
        str(trigger.get("request_id") or "").strip() == str(parent_request_id or "").strip()
        and action.get("ready") is True
        and str(action.get("status") or "").upper() == "READY"
        and action.get("request_scoped_pit_resolved") is True
        and freshness.get("decision_request_time")
        and freshness.get("refresh_mode") in {"QUERY_TIME_IMMEDIATE_REFRESH", "REUSED_REQUEST_BOUND_FACTS"}
        and freshness.get("status") in {"DIRECT", "REUSED", "READY"}
    )


def _snapshot_is_valid_for_decision(market_date: str, snapshot: dict, market_fact_cutoff: datetime, availability_cutoff: datetime) -> tuple[bool, str]:
    """Validate independent market-fact and snapshot-availability cutoffs."""
    if snapshot.get("market_date") != market_date or snapshot.get("quality_status") != "PASS":
        return False, "SNAPSHOT_IDENTITY_OR_QUALITY_INVALID"
    captured = parse_time(snapshot.get("captured_at_beijing") or snapshot.get("captured_at"))
    if captured is None or captured > availability_cutoff:
        return False, "SNAPSHOT_NOT_AVAILABLE_BY_PERSISTENCE_BOUNDARY"
    if any(value > market_fact_cutoff for value in _snapshot_fact_times(snapshot)):
        return False, "SNAPSHOT_MARKET_FACT_AFTER_DECISION_CUTOFF"
    return True, ""

def select_point_in_time_snapshot(market_date: str, decision_time: str, *, availability_time: str = "", consumed_snapshot: str = "") -> tuple[str, dict, str]:
    """Select a legal snapshot using separate fact and availability clocks."""
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
    ranked = sorted([x for x in items if x.get("change_pct") is not None], key=lambda x: x["change_pct"], reverse=True)
    rank_map = {x["code"]: i + 1 for i, x in enumerate(ranked)}
    for item in items:
        item["descriptive_daily_return_rank"] = rank_map.get(item["code"])
    comparison = {
        "as_of_beijing": snapshot.get("captured_at_beijing"),
        "market_phase": snapshot.get("market_phase"),
        "etf_count": len(items),
        "items": items,
        "state_persistence": build_state_persistence_projection(snapshot, names),
        "interpretation_rule": "保留正式决策时点的全ETF横截面证据；EVENT/DELTA 与 STATE/PERSISTENCE 并存用于后续买入候选与已有持仓卖出链的证据消费。当日涨跌排名只是描述维度，不是资本效率评分，不生成轮动动作。",
    }
    return comparison


def build_state_persistence_projection(snapshot: dict, names: dict[str, str]) -> dict:
    """Project existing multi-horizon context into the formal comparison evidence.

    This is not a new state store: the existing market_structure_context remains
    the canonical owner.  The projection is strictly PIT-bounded and fail-safe.
    """
    cutoff = parse_time(snapshot.get("captured_at_beijing") or snapshot.get("captured_at"))
    context_path = ROOT / "data" / "state" / "market_structure_context.json"
    context = load_json(context_path) if context_path.exists() else {}
    delta_path = ROOT / "data" / "state" / "research_evidence_delta.json"
    delta = load_json(delta_path) if delta_path.exists() else {}
    context_as_of = parse_time(context.get("as_of_beijing"))
    delta_as_of = parse_time(delta.get("as_of_beijing"))
    context_quality_ready = context.get("status") == "READY" and bool(context.get("items"))
    context_time_valid = bool(cutoff and context_as_of and context_as_of <= cutoff)
    delta_time_valid = bool(cutoff and delta_as_of and delta_as_of <= cutoff)
    by_code = {
        str(item.get("code")): item
        for item in (context.get("items") or [])
        if isinstance(item, dict) and item.get("code")
    }
    delta_by_code = {
        str(item.get("code")): item
        for item in (delta.get("items") or [])
        if isinstance(item, dict) and item.get("code")
    }
    projection_items = []
    for code, name in names.items():
        item = by_code.get(code)
        item_as_of = parse_time(item.get("as_of_beijing")) if item else None
        if not item:
            status = "MISSING"
        elif not context_quality_ready or not context_time_valid or item_as_of is None or item_as_of > cutoff:
            status = "STALE_OR_UNVERIFIABLE"
        else:
            status = "READY"
        historical = item.get("historical_context") if status == "READY" else {}
        turnover = item.get("turnover_acceptance_context") if status == "READY" else {}
        participation = item.get("participation_structure_confirmation") if status == "READY" else {}
        projection_items.append({
            "code": code,
            "name": name,
            "status": status,
            "as_of_beijing": item.get("as_of_beijing") if item else None,
            "source": "data/state/market_structure_context.json" if item else None,
            "historical": {
                "latest_history_date": historical.get("latest_history_date"),
                "return_vs_5_sessions_ago_pct": historical.get("return_vs_5_sessions_ago_pct"),
                "return_vs_20_sessions_ago_pct": historical.get("return_vs_20_sessions_ago_pct"),
                "window_20": historical.get("window_20"),
                "window_60": historical.get("window_60"),
                "historical_zone": historical.get("historical_zone"),
                "trend_state": historical.get("trend_state"),
                "trend_detail": historical.get("trend_detail"),
            },
            "participation": {
                "status": participation.get("status") or turnover.get("status"),
                "completed_bar_date": participation.get("completed_bar_date"),
                "participation_ratio_vs_prior_20d": (
                    participation.get("participation_ratio_vs_prior_20d")
                    if participation.get("participation_ratio_vs_prior_20d") is not None
                    else turnover.get("time_normalized_amount_pace_ratio")
                ),
                "acceptance_behavior": (
                    participation.get("acceptance_behavior")
                    or turnover.get("acceptance_behavior")
                ),
                "enhancement_active": participation.get("enhancement_active"),
            },
            "event_delta": (
                delta_by_code.get(code, {})
                if status == "READY" and delta_time_valid
                else {}
            ),
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
            "buy_candidate": {
                "consumer": "existing formal decision and research evidence path",
                "use": "candidate comparison evidence only",
                "can_generate_action_independently": False,
            },
            "held_position_sell": {
                "consumer": "existing formal decision and research evidence path",
                "use": "held-position evidence and opportunity-cost comparison only",
                "can_generate_action_independently": False,
            },
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


RESEARCH_EVIDENCE_EFFECTS = {
    "ENHANCE",
    "WEAKEN",
    "SUPPORT_HOLD",
    "SUPPORT_NO_ADD",
    "SUPPORT_RELEASE_REVIEW",
    "CONTEXT_ONLY",
}


def validate_research_evidence_used(value: object, object_name: str = "formal_decision.research_evidence_used") -> str:
    """Validate only explicitly supplied ex-ante research usage facts."""
    if value is None:
        return ""
    if not isinstance(value, list):
        return f"{object_name} must be a list"
    if len(value) > 3:
        return f"{object_name} supports at most 3 evidence items"
    for index, item in enumerate(value):
        prefix = f"{object_name}[{index}]"
        if not isinstance(item, dict):
            return f"{prefix} must be an object"
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            return f"{prefix}.evidence_id is required"
        effect = str(item.get("decision_effect") or "").strip().upper()
        if effect not in RESEARCH_EVIDENCE_EFFECTS:
            return f"{prefix}.decision_effect is not a registered value"
        if "decisive" in item and not isinstance(item.get("decisive"), bool):
            return f"{prefix}.decisive must be boolean"
        security_code = str(item.get("security_code") or "").strip()
        if security_code and not re.fullmatch(r"\d{6}", security_code):
            return f"{prefix}.security_code must be a 6-digit code"
        evidence_as_of = item.get("evidence_as_of_beijing")
        if evidence_as_of not in (None, "") and parse_time(evidence_as_of) is None:
            return f"{prefix}.evidence_as_of_beijing must be a valid timestamp"
        source = item.get("source")
        if source is not None and not isinstance(source, str):
            return f"{prefix}.source must be text"
    return ""


def record_formal_decision(request: dict) -> tuple[bool, str]:
    decision = request.get("formal_decision")
    if not isinstance(decision, dict) or not decision:
        return False, ""
    decision = dict(decision)
    original_opportunity_status = str(decision.get("opportunity_status") or "").strip()
    canonical_opportunity_status = _canonical_formal_opportunity_status(original_opportunity_status)
    if canonical_opportunity_status and canonical_opportunity_status != original_opportunity_status:
        decision["opportunity_status"] = canonical_opportunity_status
        decision.setdefault("opportunity_status_detail", original_opportunity_status)
    contract_error = validate_formal_decision_contract(decision)
    if contract_error:
        raise ValueError(f"invalid formal decision contract: {contract_error}")
    research_error = validate_research_evidence_used(decision.get("research_evidence_used"))
    if research_error:
        raise ValueError(f"invalid formal decision research-evidence contract: {research_error}")
    account_for_lifecycle = _account_for_decision_validation(request)
    current_path = ROOT / "data/state/CURRENT.json"
    current = load_json(current_path) if current_path.exists() else {}
    market_date = str(request.get("market_date") or current.get("market_date") or "")
    managed_error = validate_managed_position_lifecycle(decision.get("lifecycle"), account_for_lifecycle, "formal_decision.lifecycle")
    if managed_error:
        raise ValueError(f"invalid formal decision managed-position contract: {managed_error}")
    review_error = validate_managed_position_review_contract(
        decision.get("managed_position_reviews"), account_for_lifecycle,
        "formal_decision.managed_position_reviews",
    )
    if review_error:
        raise ValueError(f"invalid formal decision managed-position review contract: {review_error}")
    managed_projection = build_managed_position_projection(ROOT, account_for_lifecycle)
    monitor_path = ROOT / "config/market/etf_monitor_universe.json"
    if monitor_path.exists():
        monitor_universe = load_json(monitor_path)
        monitored_codes = {str(x.get("code") or "") for x in (monitor_universe.get("objects") or [])}
        held_etfs = set(active_account_asset_codes(ROOT, account_for_lifecycle).get("etf", set()))
        current_observations = monitored_codes - held_etfs
    else:
        monitored_codes = set()
        held_etfs = set()
        current_observations = set()
    discovery_inputs = request_bound_discovery_eligibility_inputs(ROOT, market_date, request)
    closure_inputs, closure_error = manual_completion_discovery_inputs(request)
    if closure_error:
        raise ValueError(closure_error)
    if closure_inputs is not None:
        discovery_inputs = closure_inputs
    identity_review_inputs = request_bound_observation_identity_review_inputs(request)
    overlapping_identity = set(discovery_inputs) & set(identity_review_inputs)
    if overlapping_identity:
        raise ValueError("explicit Observation identity review duplicates same-node Discovery input: " + ", ".join(sorted(overlapping_identity)))
    eligibility_inputs = {**discovery_inputs, **identity_review_inputs}
    admitted_discovery, eligibility_error = validate_observation_eligibility_reviews(decision, eligibility_inputs, identity_review_codes=set(identity_review_inputs))
    if eligibility_error:
        raise ValueError(f"invalid formal decision Observation eligibility contract: {eligibility_error}")
    if decision.get("observation_management") not in (None, []) or current_observations or admitted_discovery:
        if not monitor_path.exists():
            raise ValueError("observation management requires canonical ETF monitor universe")
        changes = validate_observation_management(
            decision,
            held_codes=held_etfs,
            monitored_codes=monitored_codes,
            require_existing_coverage=bool(current_observations),
            allowed_admit_codes=admitted_discovery,
        )
        actual_admits = {normalize_code(x.get("code") or "") for x in changes if str(x.get("action") or "").upper() == "ADMIT"}
        if actual_admits != admitted_discovery:
            raise ValueError("formal decision ADMIT actions must exactly match Observation eligibility ADMIT dispositions")
    # Capital-competition coverage belongs to the request-bound Decision Work
    # Package.  ADMIT/REJECT controls Observation identity only; a REJECTed
    # Discovery object was still a legal temporary evaluation object that had to
    # participate in this request's capital competition.
    required_opportunities = request_bound_etf_opportunity_reviews(request)
    if not required_opportunities:
        # Legacy completions predate the Decision Work Package contract.  Keep
        # their historical compatibility path without weakening new production.
        required_opportunities = legal_observation_reviews(decision, current_observations, admitted_discovery)
    capital_error = validate_capital_competition_contract(
        decision.get("capital_competition"), account_for_lifecycle,
        "formal_decision.capital_competition",
        required_etf_opportunities=required_opportunities,
    )
    if capital_error:
        raise ValueError(f"invalid formal decision capital-competition contract: {capital_error}")
    consistency_error = validate_formal_decision_cross_field_consistency(
        decision, account_for_lifecycle, "formal_decision",
    )
    if consistency_error:
        raise ValueError(f"invalid formal decision cross-field consistency: {consistency_error}")
    main_candidate = str(decision.get("main_candidate") or "")
    explicit_code = str(decision.get("candidate_code") or decision.get("code") or "")
    match = re.search(r"（(\d{6})）", main_candidate) or re.search(r"(?<!\d)(\d{6})(?!\d)", main_candidate)
    code = explicit_code or (match.group(1) if match else "")
    name = str(decision.get("candidate_name") or "")
    if not name and code:
        name_match = re.search(rf"([^｜+，,；;]+?)（{re.escape(code)}）", main_candidate)
        if name_match:
            name = name_match.group(1).strip()
    request_id = str(request.get("request_id") or "").strip()
    is_manual_completion = str(request.get("source") or "").strip() == "CHATGPT_MANUAL_FORMAL_COMPLETION"
    parent_request_id = str(request.get("parent_request_id") or "").strip()
    if is_manual_completion and (not parent_request_id or not request_id or parent_request_id == request_id):
        raise ValueError("manual formal completion requires distinct envelope and parent request identities")
    manual_parent_request = _manual_completion_parent_request(request) if is_manual_completion else {}
    if is_manual_completion:
        query_path = ROOT / "data/state/query_context.json"
        if not query_path.exists():
            raise ValueError("manual formal completion requires request-scoped query context")
        query_context = load_json(query_path)
        packet = query_context.get("decision_fact_pack") or {}
        action_readiness = packet.get("formal_action_readiness") or packet.get("formal_reasoning_readiness") or {}
        packet_request_id = str((packet.get("trigger") or {}).get("request_id") or "").strip()
        if packet_request_id not in {parent_request_id, request_id}:
            raise ValueError("manual formal completion query context is not bound to parent/completion request")
        if action_readiness.get("ready") is not True or str(action_readiness.get("status") or "").upper() != "READY":
            blockers = ",".join(str(x) for x in (action_readiness.get("blockers") or [])) or "UNKNOWN"
            raise ValueError(f"manual formal completion action inputs are not READY: {blockers}")
    fingerprint_request_id = parent_request_id if is_manual_completion else request_id
    fingerprint = hashlib.sha256(json.dumps({"request_id": fingerprint_request_id, "market_date": market_date, "formal_decision": decision}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    decision_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(decision.get("decision_id") or request_id or f"{market_date}_{fingerprint[:12]}"))

    decision_time = str(decision.get("data_as_of_beijing") or "")
    if not decision_time:
        decision_time = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    availability_time = str(
        request.get("persistence_available_at_beijing")
        or request.get("decision_persisted_at_beijing")
        or decision.get("issued_at_beijing")
        or decision.get("decision_effective_at_beijing")
        or request.get("requested_at_beijing")
        or ""
    )
    consumed_snapshot = str(
        request.get("consumed_snapshot")
        or request.get("consumed_snapshot_path")
        or decision.get("consumed_snapshot")
        or decision.get("consumed_snapshot_path")
        or ""
    )
    external_evidence = None
    request_type = str(request.get("request_type") or "").upper()
    external_path = str(request.get("consumed_external_market_evidence") or "").strip()
    if request_type == "EMERGENCY_EXTERNAL_MARKET_EVIDENCE":
        if consumed_snapshot:
            raise ValueError("external evidence and consumed_snapshot are mutually exclusive")
        external_evidence = validate_external_market_evidence(
            request, ROOT, decision_time=decision_time,
            availability_time=availability_time,
            ingress_path=str(request.get("_ingress_path") or ""),
        )
        snapshot_rel = external_evidence["path"]
        snapshot = {
            "market_date": external_evidence["market_date"],
            "captured_at_beijing": external_evidence["retrieved_at_beijing"],
            "quality_status": "PASS",
            "rows": external_evidence["rows"],
        }
        pit_status = "EXTERNAL_MARKET_EVIDENCE_VALIDATED"
    elif external_path:
        raise ValueError("external evidence path requires EMERGENCY_EXTERNAL_MARKET_EVIDENCE request_type")
    else:
        snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(
            market_date, decision_time, availability_time=availability_time, consumed_snapshot=consumed_snapshot
        )
    if is_manual_completion and (
        not snapshot_rel
        or pit_status not in {
            "CONSUMED_SNAPSHOT_VALIDATED",
            "POINT_IN_TIME_SNAPSHOT_TWO_CLOCK_VALIDATED",
            "EXTERNAL_MARKET_EVIDENCE_VALIDATED",
        }
    ):
        raise ValueError(f"manual formal decision requires legal PIT/source snapshot: {pit_status}")
    request_bound_query_time_pit = False
    if is_manual_completion and not external_evidence and not _manual_completion_snapshot_is_post_request(
        manual_parent_request, snapshot
    ):
        request_bound_query_time_pit = (
            _manual_completion_envelope_pit_ready(request, manual_parent_request, parent_request_id)
            or _manual_completion_request_bound_pit_ready(query_context, parent_request_id)
        )
        if not request_bound_query_time_pit:
            raise ValueError(
                "manual formal decision requires a qualified post-request snapshot "
                "or a READY request-bound query-time PIT closure"
            )
        pit_status = "REQUEST_BOUND_QUERY_TIME_PIT_WITH_PRIOR_CANONICAL_SNAPSHOT"

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
        evidence_row = next(
            (x for x in external_evidence["rows"]
             if str(x.get("symbol") or "").split(".")[0] == code.split(".")[0]),
            None,
        )
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
    event = {"event_type": "FORMAL_DECISION", "decision_id": decision_id, "request_id": request_id, "parent_request_id": parent_request_id or None, "fingerprint": fingerprint, "market_date": market_date, "decision_time_beijing": decision_time, "decision_effective_at_beijing": str(decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing") or ""), "decision_effective_ordering": str(decision.get("decision_effective_ordering") or ""), "timing_quality": str(decision.get("timing_quality") or ""), "timing_provenance": str(decision.get("timing_provenance") or ""), "interaction_scenario": request.get("interaction_scenario"), "candidate_code": code, "candidate_name": name, "hypothesis_id": hypothesis_id, "hypothesis_link_status": hypothesis_link_status, "hypothesis_closed": hypothesis_closed, "price_at_decision": price_at_decision, "price_as_of_beijing": price_as_of, "price_source_snapshot": snapshot_rel, "price_source": price_source, "point_in_time_status": pit_status, "comparison_snapshot": comparison, "formal_decision": decision, "managed_position_sell_review": managed_projection, "read_only_research_event": True, "decision_boundary": "只保存ChatGPT已经形成的正式决策和决策时点可见证据。禁止使用决策时点之后的行情回填价格或比较快照；研究留痕用于验证候选选择、假设生命周期、判断与执行质量，不自行推导交易权限。", "market_evidence_type": "EMERGENCY_EXTERNAL_MARKET_EVIDENCE" if external_evidence else "CANONICAL_SNAPSHOT", "external_evidence_path": external_evidence["path"] if external_evidence else "", "external_evidence_id": external_evidence["evidence_id"] if external_evidence else "", "external_evidence_validation": external_evidence["validation_status"] if external_evidence else "",
        "request_bound_query_time_pit": request_bound_query_time_pit,
        "request_bound_query_time_pit_source": (
            "completion_envelope.request_bound_pit_closure"
            if request_bound_query_time_pit and _manual_completion_envelope_pit_ready(request, manual_parent_request, parent_request_id)
            else ("data/state/query_context.json" if request_bound_query_time_pit else "")
        ),
        "external_evidence_scope": external_evidence.get("evidence_scope", "") if external_evidence else "",
        "decision_evidence_eligibility": external_evidence.get("decision_evidence_eligibility", "") if external_evidence else "",
        "execution_price_eligibility": external_evidence.get("execution_price_eligibility", "") if external_evidence else "",
        "execution_revalidation_required": bool(external_evidence.get("execution_revalidation_required")) if external_evidence else False,
        "standalone_action_allowed": external_evidence.get("standalone_action_allowed") if external_evidence else None,
        "execution_boundary": (
            "人工执行前必须刷新当前最新行情并重新确认；条件失效时不得执行原计划"
            if external_evidence and external_evidence.get("execution_price_eligibility") != "EXECUTION_PRICE_ELIGIBLE"
            else ""
        ),
        "source_type": request.get("request_type") if str(request.get("request_type") or "").upper() == BUSINESS_DECISION_SOURCE else "", "source_fingerprint": request.get("_source_fingerprint") or "", "reply_ready": bool(request.get("_source_fingerprint")), "presentation_binding": _formal_decision_presentation_binding(request, request_id, decision_id), "recorded_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
    # New production Formal Decision delivery must never use the historical
    # events/decisions writer. Its only canonical publication is the separate
    # completion ingress, which writes events/reviews/formal_<occurrence_id>.json.
    if str(request.get("request_type") or "").strip().upper() == BUSINESS_DECISION_SOURCE:
        raise ValueError(
            "new Formal Decision production source cannot use legacy events/decisions writer"
        )
    if (
        str(request.get("request_type") or "").strip().upper() == "SCHEDULED_REVIEW_COMPLETION"
        and str(request.get("source") or "").strip().upper() == "CHATGPT_SCHEDULED_REVIEW_ACTOR"
        and str(request.get("formal_fact_type") or "").strip().upper() == "FORMAL_POST_CLOSE_REVIEW"
        and str(request.get("task_id") or "").strip() == "ETF正式决策"
    ):
        raise ValueError(
            "Formal Decision completion must use events/reviews/formal_<occurrence_id>.json owner"
        )
    event_path = ROOT / "events/decisions" / f"{decision_id}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    if event_path.exists():
        prior = load_json(event_path)
        if str(request.get("request_type") or "").upper() == BUSINESS_DECISION_SOURCE:
            prior_sour…29230 tokens truncated…or (scenario == "POST_CLOSE_REVIEW" and not is_decision_fact and not is_trade_fact)
    )
    is_report_delivery = channel in {"REPORT", "REPORT_DELIVERY"} or report_type in {"ETF_TRADE_REVIEW", "SCHEDULED_TRADE_REVIEW"}

    if not any([is_broker_fact, is_review_fact, is_trade_fact, is_decision_fact]):
        return {
            "required": False,
            "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
            "reason": "not_a_formal_fact_ingress_request",
        }

    receipt = request.get("_ingress_path") or request.get("canonical_request_path") or request.get("request_path")
    submission_result = request.get("canonical_ingress_result") or request.get("canonical_submission_result")
    if isinstance(submission_result, dict):
        result_status = str(submission_result.get("status") or submission_result.get("terminal_state") or "").strip().upper()
        result_receipt = submission_result.get("receipt") or submission_result.get("receipt_path") or submission_result.get("commit_sha")
        if result_receipt and result_status in {"SUBMITTED", "SUCCESS", CANONICAL_INGRESS_SUBMITTED}:
            receipt = result_receipt
    if not receipt:
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_durable_ingress_receipt",
        }

    if invalid_trade_events:
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "invalid_trade_events",
        }

    if is_broker_fact:
        if isinstance(request.get("account_fact"), dict) or isinstance(request.get("trade_event"), dict) or has_trade_events:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "broker_account_fact_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_account_fact",
        }

    if is_review_fact:
        if isinstance(request.get("formal_review"), dict) or isinstance(request.get("review"), dict):
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "post_close_review_request_submitted",
            }
        if is_report_delivery:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
                "reason": "report_delivery_is_not_canonical_review_ingress",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_formal_review",
        }

    if is_trade_fact:
        if isinstance(request.get("trade_event"), dict) or has_trade_events:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "confirmed_trade_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_trade_event",
        }

    if is_decision_fact:
        if isinstance(request.get("formal_decision"), dict):
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "formal_decision_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_formal_decision",
        }

    return {
        "required": False,
        "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
        "reason": "not_a_formal_fact_ingress_request",
    }


def is_broker_screenshot_request(request: dict) -> bool:
    """Recognize broker facts from an explicit contract, with legacy compatibility.

    Free-form provenance remains metadata; it is not the business contract.
    The historical query-time request is accepted because its stable reason and
    ChatGPT origin explicitly identify a broker screenshot adoption.
    """
    explicit = _explicit_fact_type(request)
    if explicit in {"BROKER_ACCOUNT_SNAPSHOT", "ACCOUNT_FACT_CONFIRMATION", "BROKER_SCREENSHOT"}:
        return True
    if str(request.get("interaction_scenario") or "").strip().upper() == "BROKER_SCREENSHOT_SYNC":
        return True
    if str(request.get("source") or "").strip().upper() == "CHATGPT_USER_BROKER_SCREENSHOT":
        return True
    reason = str(request.get("reason") or "").strip().upper()
    requested_by = str(request.get("requested_by") or "").strip().lower()
    return (
        requested_by == "chatgpt"
        and reason == "USER_BROKER_SCREENSHOT_QUERY_TIME_REFRESH"
    )


def validate_broker_account_fact_ingress(request: dict, supplied: object) -> str:
    """Fail fast on malformed ChatGPT-translated broker account facts.

    This validates transport shape only. It does not infer security identity,
    trading permission, or market facts from names.
    """
    if not is_broker_screenshot_request(request):
        return ""
    if supplied is None:
        # A broker screenshot request must carry the canonical account_fact
        # envelope. Silently accepting account_after_screenshot (or any other
        # non-canonical field) leaves the request durable but unconsumed and
        # makes the downstream gate appear stuck.
        if "account_after_screenshot" in request or "positions" in request:
            return "broker screenshot request must provide account_fact"
        return ""
    if not isinstance(supplied, dict):
        return "broker account_fact must be an object"
    positions = supplied.get("positions")
    if positions is None:
        return ""
    if not isinstance(positions, list):
        return "broker account_fact.positions must be a list"
    seen: set[str] = set()
    for index, position in enumerate(positions):
        if not isinstance(position, dict):
            return f"broker account_fact.positions[{index}] must be an object"
        code = str(position.get("code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return f"broker account_fact.positions[{index}].code must be a six-digit security code"
        if code in seen:
            return f"broker account_fact.positions contains duplicate code {code}"
        seen.add(code)
        if position.get("quantity") is None:
            return f"broker account_fact.positions[{index}].quantity is required"
        try:
            float(position.get("quantity"))
        except (TypeError, ValueError):
            return f"broker account_fact.positions[{index}].quantity must be numeric"
        asset_type = str(position.get("asset_type") or "").strip().upper()
        if asset_type and asset_type not in {"ETF", "STOCK", "FUND", "OTHER"}:
            return f"broker account_fact.positions[{index}].asset_type is invalid"
    return ""


def merge_account_fact(prior: dict, supplied: dict) -> dict:
    """Merge confirmed screenshot facts and derive settlement cash canonically."""
    merged = json.loads(json.dumps(prior or {}))
    for key, value in (supplied or {}).items():
        if key != "settlement_obligations" and value is not None:
            merged[key] = json.loads(json.dumps(value))
    # An ordinary screenshot is a partial observation.  Empty history-shaped
    # defaults from an ingress adapter must not erase canonical trade,
    # decision, fee, lifecycle, or reconciliation facts.
    for key in ("formal_action", "orders", "trades", "fee_facts", "account_reconciliation",
                "account_change_events_after_confirmed_at", "lifecycle", "confirmed_trades",
                "formal_decision", "reconciliation_metadata"):
        if key in (prior or {}) and (key not in (supplied or {}) or supplied.get(key) in (None, [], {})):
            merged[key] = json.loads(json.dumps(prior[key]))
    merged["settlement_obligations"] = _merge_settlement_obligations(prior or {}, supplied or {})
    for key in ("formal_action", "orders", "trades", "fee_facts", "account_reconciliation",
                "account_change_events_after_confirmed_at", "lifecycle", "confirmed_trades",
                "formal_decision", "reconciliation_metadata"):
        if key not in merged and key in (prior or {}):
            merged[key] = json.loads(json.dumps(prior[key]))
    _canonicalize_settlement_account(merged)
    _annotate_confirmed_ipo_origins(merged)
    _reconcile_known_ipo_events(merged)
    return merged


def process_historical_backfill_request(request: dict) -> dict:
    """Persist confirmed historical trade facts without touching current account state."""
    if str(request.get("ingress_mode") or "").upper() != "HISTORICAL_BACKFILL":
        raise ValueError("historical backfill mode is required")
    trades = request.get("historical_trades")
    if not isinstance(trades, list) or not trades:
        raise ValueError("historical_trades must be a non-empty list")
    trade_dir = ROOT / "events" / "trades"
    trade_dir.mkdir(parents=True, exist_ok=True)
    created = enriched = 0
    for raw in trades:
        if not isinstance(raw, dict):
            raise ValueError("historical trade must be an object")
        code, side = str(raw.get("code") or "").strip(), str(raw.get("side") or "").upper()
        quantity, price = safe_float(raw.get("quantity")), safe_float(raw.get("price"))
        executed_at = str(raw.get("executed_at") or raw.get("executed_at_beijing") or "").strip()
        confirmed_at = str(raw.get("confirmed_at_beijing") or "").strip()
        adoption_at = str(raw.get("historical_fact_adopted_at") or request.get("historical_fact_adopted_at") or datetime.now(SHANGHAI).isoformat(timespec="seconds")).strip()
        market_date = str(raw.get("market_date") or executed_at[:10]).strip()
        if not code or side not in {"BUY", "SELL"} or quantity is None or price is None or not executed_at or not adoption_at or not market_date:
            raise ValueError("historical trade core fields are incomplete")
        event_id = str(raw.get("event_id") or f"HISTORICAL_{market_date}_{code}_{side}_{format(quantity, '.12g')}_{format(price, '.12g')}")
        key = str(raw.get("idempotency_key") or f"HISTORICAL_BACKFILL|{code}|{side}|{format(quantity, '.12g')}|{format(price, '.12g')}|{executed_at}")
        path = trade_dir / f"{event_id}.json"
        existing = load_json(path) if path.exists() else None
        if existing:
            for field, expected in {"code": code, "side": side, "quantity": quantity, "price": price}.items():
                actual = existing.get(field)
                numeric_field = field in {"quantity", "price"}
                normalized_actual = safe_float(actual) if numeric_field else actual
                normalized_expected = expected
                mismatch = (
                    normalized_actual is None or normalized_actual != normalized_expected
                    if numeric_field
                    else str(actual) != str(expected)
                )
                if mismatch:
                    source_path = str(request.get("_ingress_path") or "events/trades/" + f"{event_id}.json")
                    detail = (
                        f"historical core mismatch: {event_id}:{field}; "
                        f"existing_value={actual!r}; existing_type={type(actual).__name__}; "
                        f"expected_value={expected!r}; expected_type={type(expected).__name__}; "
                        f"existing_source_path={source_path}; request_id={request.get('request_id')!r}; "
                        f"normalized_existing={normalized_actual!r}; normalized_existing_type={type(normalized_actual).__name__}; "
                        f"normalized_expected={normalized_expected!r}; normalized_expected_type={type(normalized_expected).__name__}"
                    )
                    raise ValueError(detail)
            existing_executed_at = existing.get("executed_at") or existing.get("executed_at_beijing")
            if existing_executed_at and str(existing_executed_at) != executed_at:
                source_path = str(request.get("_ingress_path") or "events/trades/" + f"{event_id}.json")
                raise ValueError(
                    f"historical core mismatch: {event_id}:executed_at; "
                    f"existing_value={existing_executed_at!r}; existing_type={type(existing_executed_at).__name__}; "
                    f"expected_value={executed_at!r}; expected_type={type(executed_at).__name__}; "
                    f"existing_source_path={source_path}; request_id={request.get('request_id')!r}; "
                    f"normalized_existing={str(existing_executed_at)!r}; normalized_existing_type=str; "
                    f"normalized_expected={executed_at!r}; normalized_expected_type=str"
                )
            changed = False
            if not existing.get("historical_backfill") and confirmed_at:
                existing["confirmed_at_beijing"] = confirmed_at
                existing["confirmation_time_semantics"] = "RELIABLE_HISTORICAL_CONFIRMATION"
                changed = True
            for field, value in {"historical_fact_adopted_at": adoption_at, "executed_at": executed_at, "executed_at_beijing": executed_at, "market_date": market_date, "account_updated_at": raw.get("account_updated_at"), "amount": raw.get("gross_amount"), "fee_amount": raw.get("fee_amount"), "fee": raw.get("fee_amount"), "fee_status": raw.get("fee_status"), "source": raw.get("source"), "source_type": raw.get("source_type") or "user_confirmed_historical_fact", "source_period": request.get("source_period") or "2026-07-13..2026-09-15", "historical_execution_time_preserved": True, "historical_backfill": True}.items():
                if value not in (None, "") and existing.get(field) != value:
                    existing[field] = value
                    changed = True
            if changed:
                atomic_json_write(path, existing)
                enriched += 1
            continue
        event = {
            "event_id": event_id, "idempotency_key": key, "event_type": "HISTORICAL_BACKFILL_TRADE",
            "historical_backfill": True, "code": code, "side": side, "quantity": quantity, "price": price,
            "executed_at": executed_at, "executed_at_beijing": executed_at,
            "confirmed_at_beijing": confirmed_at or adoption_at, "historical_fact_adopted_at": adoption_at, "confirmation_time_semantics": "RELIABLE_HISTORICAL_CONFIRMATION" if confirmed_at else "CANONICAL_ADOPTION_TIME", "market_date": market_date,
            "account_updated_at": raw.get("account_updated_at"), "amount": raw.get("gross_amount", raw.get("amount")),
            "fee": raw.get("fee", raw.get("fee_amount")), "fee_amount": raw.get("fee_amount"), "fee_status": raw.get("fee_status"), "name": raw.get("name"),
            "source": raw.get("source") or "USER_CONFIRMED_HISTORICAL_SCREENSHOT",
            "source_type": raw.get("source_type") or "user_confirmed_historical_fact",
            "source_period": request.get("source_period") or "2026-07-13..2026-09-15",
            "historical_execution_time_preserved": True,
            "source_confidence": raw.get("source_confidence") or "USER_CONFIRMED",
            "execution_status": "EXECUTED", "replay_semantics": "FACT_ENRICHMENT_ONLY",
        }
        atomic_json_write(path, event)
        created += 1
    acquisition = request.get("acquisition")
    if acquisition:
        if not isinstance(acquisition, dict) or str(acquisition.get("code")) != "301689":
            raise ValueError("invalid acquisition contract")
        aid = str(acquisition.get("event_id") or "HISTORICAL_ACQUISITION_20260901_301689_500")
        apath = trade_dir / f"{aid}.json"
        if not apath.exists():
            atomic_json_write(apath, {
                "event_id": aid, "event_type": "IPO_ALLOTMENT_ACQUISITION",
                "historical_backfill": True, "asset_type": "STOCK", "code": "301689",
                "quantity": 500, "price": 16.0, "market_date": "2026-09-01",
                "executed_at": acquisition.get("executed_at") or "2026-09-01T00:00:00+08:00",
                "confirmed_at_beijing": acquisition.get("confirmed_at_beijing") or adoption_at,
                "historical_fact_adopted_at": adoption_at,
                "confirmation_time_semantics": "RELIABLE_HISTORICAL_CONFIRMATION" if acquisition.get("confirmed_at_beijing") else "CANONICAL_ADOPTION_TIME",
                "source_type": acquisition.get("source_type") or "user_confirmed_historical_fact",
                "source_period": request.get("source_period") or "2026-07-13..2026-09-15",
                "historical_execution_time_preserved": True,
                "lot_source": "confirmed_settlement_obligation",
                "source": acquisition.get("source") or "USER_CONFIRMED_HISTORICAL_SCREENSHOT",
                "execution_status": "ACQUIRED",
            })
            created += 1
        for path in trade_dir.glob("*.json"):
            event = load_json(path)
            if (str(event.get("code") or "") != "301689"
                    or str(event.get("side") or "").upper() != "SELL"
                    or safe_float(event.get("quantity")) != 500.0):
                continue
            if event.get("lot_source_event_id") != aid:
                event["lot_source_event_id"] = aid
                event["lot_source_relation"] = "ACQUISITION_TO_SELL"
                atomic_json_write(path, event)
                enriched += 1
    return {"ok": True, "ingress_mode": "HISTORICAL_BACKFILL", "created": created, "metadata_enriched": enriched, "current_account_mutated": False, "current_state_mutated": False}

def account_fact_is_older(prior: dict, supplied: dict) -> bool:
    old_time = parse_time(prior.get("updated_at"))
    new_time = parse_time(supplied.get("updated_at"))
    return bool(old_time and new_time and new_time < old_time)



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    parser.add_argument("--formal-replay-source-commit", default="")
    parser.add_argument("--formal-replay-dwp-commit", default="")
    args = parser.parse_args()
    forensic_historical_replay = bool(str(args.formal_replay_source_commit or "").strip()) and os.environ.get("ETF_FORMAL_REPLAY_FORENSIC") == "1"
    reset_formal_replay_forensic_trace(forensic_historical_replay)
    raw_request_path = Path(str(args.request_path or "").strip())
    # The workflow may pass either the repository-relative ingress path or the
    # absolute path after its execution-time-main reset/recovery step. Resolve
    # both forms through the same root boundary; never allow an outside path.
    req_path = (raw_request_path if raw_request_path.is_absolute() else ROOT / raw_request_path).resolve()
    if req_path == ROOT or ROOT not in req_path.parents or not req_path.is_file():
        raise RuntimeError("invalid state sync request path")
    request = load_json(req_path)
    request["_ingress_path"] = str(req_path.relative_to(ROOT)).replace("\\\\", "/")

    # Scheduled Review actors may omit the derived scenario field even though
    # the canonical request type and formal fact type identify it
    # deterministically. Normalize only this registered ingress; do not infer
    # identity from task names, timestamps, or historical objects.
    if (
        str(request.get("request_type") or "").strip().upper()
        == "SCHEDULED_REVIEW_COMPLETION"
        and not str(request.get("interaction_scenario") or "").strip()
    ):
        scenario_by_fact_type = {
            "FORMAL_POST_CLOSE_REVIEW": "POST_CLOSE_REVIEW",
            "FORMAL_SCHEDULED_TRADE_REVIEW": "SCHEDULED_TRADE_REVIEW",
            "FORMAL_SCHEDULED_SYSTEM_REVIEW": "SCHEDULED_SYSTEM_REVIEW",
            "FORMAL_MIDDAY_REVIEW": "MIDDAY_REVIEW",
        }
        fact_type = str(request.get("formal_fact_type") or "").strip().upper()
        scenario = scenario_by_fact_type.get(fact_type)
        if not scenario:
            raise ValueError(
                "scheduled review completion requires a registered interaction_scenario"
            )
        request["interaction_scenario"] = scenario

    # New Formal Decision identities must not bypass the structured Business
    # Decision Source ingress. Historical legacy completion artifacts remain
    # readable/replayable, but a legacy envelope whose parent request was
    # created after the production cutover is rejected at the canonical
    # state-sync gateway even if a caller bypasses the CLI builder.
    legacy_manual_completion = (
        str(request.get("request_type") or "").strip().upper() == "STATE_SYNC_ONLY"
        and str(request.get("source") or "").strip().upper() == "CHATGPT_MANUAL_FORMAL_COMPLETION"
        and str(request.get("formal_fact_type") or "").strip().upper() == "FORMAL_DECISION"
    )
    if legacy_manual_completion:
        parent_id = str(request.get("parent_request_id") or "").strip()
        parent_path = ROOT / "requests" / "live_snapshot" / f"{parent_id}.json"
        parent = load_json(parent_path) if parent_id and parent_path.exists() else {}
        parent_requested = str(parent.get("requested_at_beijing") or request.get("requested_at_beijing") or "").strip()
        production_cutover = "2026-09-25T17:17:43+08:00"
        if parent_requested and parent_requested >= production_cutover:
            raise ValueError(
                "new Formal Decision legacy completion bypass is forbidden; "
                "use BUSINESS_DECISION_SOURCE with structured decision_response"
            )

    # Formal Decision completion is a separate post-BDS ingress. It reuses the
    # same canonical owner but never lets the BDS push itself create a Decision event.
    original_source_dispatch = str(request.get("request_type") or "").strip().upper() == BUSINESS_DECISION_SOURCE
    completion_dispatch = (
        str(request.get("request_type") or "").strip().upper() == "SCHEDULED_REVIEW_COMPLETION"
        and str(request.get("source") or "").strip().upper() == "CHATGPT_SCHEDULED_REVIEW_ACTOR"
        and str(request.get("formal_fact_type") or "").strip().upper() == "FORMAL_POST_CLOSE_REVIEW"
        and str(request.get("task_id") or "").strip() == "ETF正式决策"
    )
    if completion_dispatch:
        bds_path = str(request.get("bds_path") or "").strip()
        if not bds_path or ".." in bds_path:
            raise ValueError("Formal Decision completion requires a safe BDS path")
        bds_file = (ROOT / bds_path).resolve()
        if ROOT not in bds_file.parents or not bds_file.is_file():
            raise ValueError("Formal Decision completion BDS path is not durable")
        bds = load_json(bds_file)
        if str(bds.get("request_type") or "").strip().upper() != BUSINESS_DECISION_SOURCE:
            raise ValueError("Formal Decision completion must reference BUSINESS_DECISION_SOURCE")
        if str(bds.get("parent_request_id") or "").strip() != str(request.get("parent_request_id") or "").strip():
            raise ValueError("Formal Decision completion parent does not match BDS")
        if str(bds.get("decision_id") or "").strip() != str(request.get("decision_id") or "").strip():
            raise ValueError("Formal Decision completion decision_id does not match BDS")
        content = bds.get("presentation_content")
        if not isinstance(content, str) or not content:
            raise ValueError("Formal Decision completion content binding is invalid")
        binding = bds.get("presentation_binding") if isinstance(bds.get("presentation_binding"), dict) else {}
        if str(binding.get("full_content") or "") != content:
            raise ValueError("Formal Decision completion content binding is invalid")
        # BDS remains the durable source during compatibility migration. When
        # inline Formal fields are present, compare them before projecting BDS.
        for field in (
            "decision_response",
            "decision_work_package",
            "consumed_snapshot",
            "presentation_content",
            "presentation_binding",
        ):
            if field in request and request.get(field) != bds.get(field):
                raise ValueError(f"Formal Decision completion inline {field} does not match BDS")
        request.update({key: value for key, value in bds.items() if key not in {"request_id", "request_type", "source"}})
        request["request_type"] = BUSINESS_DECISION_SOURCE
        request["request_id"] = str(bds.get("request_id") or "").strip()
        request["presentation_content"] = content
        recorded, idempotent, event_path = record_formal_decision_report_event(request, bds)
        print(json.dumps({
            "ok": True,
            "request_id": request.get("request_id"),
            "decision_id": request.get("decision_id"),
            "formal_decision_event": True,
            "canonical_publication": event_path,
            "idempotent_replay": idempotent,
        }, ensure_ascii=False))
        return 0

    # Explicit Business Decision Source dispatch. The ingress file itself is
    # the immutable durable Source artifact; projection only occurs after the
    # Phase 1 gate and never calls decision logic or refreshes market facts.
    source_dispatch = str(request.get("request_type") or "").strip().upper() == BUSINESS_DECISION_SOURCE
    source_fingerprint_value = ""
    source_reply_ready = False
    if source_dispatch:
        parent_id = str(request.get("parent_request_id") or "").strip()
        if not parent_id or any(part in parent_id for part in ("/", "\\", "..")):
            raise ValueError("business decision source requires a safe parent Formal Decision request")
        parent_path = ROOT / "requests" / "live_snapshot" / f"{parent_id}.json"
        if not parent_path.exists():
            raise ValueError("business decision source parent Formal Decision request is not durable")
        parent_request = load_json(parent_path)
        if str(parent_request.get("request_id") or "").strip() != parent_id:
            raise ValueError("business decision source parent request identity mismatch")

        replay_source_commit = str(args.formal_replay_source_commit or "").strip()
        if replay_source_commit:
            if not re.fullmatch(r"[0-9a-fA-F]{40}", replay_source_commit):
                raise ValueError("formal replay source commit must be a full commit SHA")
            request_rel = str(req_path.relative_to(ROOT)).replace("\\\\", "/")
            historical_source = subprocess.run(
                ["git", "show", f"{replay_source_commit}:{request_rel}"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            if historical_source.returncode != 0 or historical_source.stdout.encode("utf-8") != req_path.read_bytes():
                raise ValueError("formal replay source bytes must match the immutable source-introduction commit")
            replay_dwp_commit = str(args.formal_replay_dwp_commit or replay_source_commit).strip()
            historical_query = subprocess.run(
                ["git", "show", f"{replay_dwp_commit}:data/state/query_context.json"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            if historical_query.returncode != 0 or not historical_query.stdout.strip():
                raise ValueError("formal replay DWP commit is missing historical request-bound query_context")
            query_context = json.loads(historical_query.stdout)
            decision_fact_pack = query_context.get("decision_fact_pack") or {}
            historical_account = decision_fact_pack.get("account_fact")
            if not isinstance(historical_account, dict) or not historical_account:
                raise ValueError("formal replay DWP commit is missing request-bound account fact")
            request["_historical_replay"] = True
            request["_historical_account_fact"] = json.loads(json.dumps(historical_account))
            # Keep discovery eligibility bound to the same immutable DWP context;
            # execution-time query_context may belong to a later request.
            request["_historical_query_context"] = query_context
        else:
            query_path = ROOT / "data" / "state" / "query_context.json"
            query_context = load_json(query_path) if query_path.exists() else {}
        packet = query_context.get("decision_fact_pack") or {}
        trigger = packet.get("trigger") or {}
        if str(trigger.get("request_id") or "").strip() != parent_id:
            # BUSINESS_DECISION_SOURCE is itself the durable actor handoff.  The
            # workflow may reset the workspace to latest main after the
            # request-bound query_context was produced; in that case the
            # execution-time projection must recover the same DWP from this
            # immutable BDS instead of accepting a later/empty DWP or failing
            # on a workspace-handoff artifact.  This is not a new request,
            # refresh, fact producer, or decision identity.
            bds_dwp = request.get("decision_work_package")
            request_type = str(request.get("request_type") or "").upper()
            if request_type == BUSINESS_DECISION_SOURCE and isinstance(bds_dwp, dict) and bds_dwp.get("problem_graph"):
                recovered_packet = dict(packet) if isinstance(packet, dict) else {}
                recovered_trigger = dict(trigger) if isinstance(trigger, dict) else {}
                recovered_trigger["request_id"] = parent_id
                recovered_packet["trigger"] = recovered_trigger
                recovered_packet["decision_work_package"] = json.loads(json.dumps(bds_dwp))
                query_context = dict(query_context) if isinstance(query_context, dict) else {}
                query_context["decision_fact_pack"] = recovered_packet
                packet = recovered_packet
                trigger = recovered_trigger
                request["_dwp_recovered_from_durable_bds"] = True
            else:
                qualifier = "historical" if replay_source_commit else "current"
                raise ValueError(f"business decision source requires {qualifier} request-bound Decision Work Package")
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("A_HISTORICAL_PACKET", obj=packet, dwp=packet.get("decision_work_package"), object_type="query_context.decision_fact_pack", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
            emit_formal_replay_forensic_fingerprint("B_PACKET_DWP", obj=packet, dwp=packet.get("decision_work_package"), object_type="packet.decision_work_package", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
        # Historical replay is provenance-bound: only the DWP loaded from the
        # explicitly selected historical query_context may define the problem graph.
        # A request-local/current DWP must never override that PIT boundary.
        if replay_source_commit:
            decision_work_package = packet.get("decision_work_package") or {}
        else:
            decision_work_package = request.get("decision_work_package") or packet.get("decision_work_package") or {}
        if not isinstance(decision_work_package, dict) or not decision_work_package.get("problem_graph"):
            raise ValueError("business decision source Decision Work Package is missing")
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("C_REQUEST_DWP_FREEZE", obj=request, dwp=decision_work_package, object_type="request.decision_work_package", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
        # Freeze the already-validated request-bound opportunity domain onto the
        # in-memory ingress object. record_formal_decision() must validate the
        # deterministic projection against the exact DWP that produced it,
        # including REJECTed Discovery evaluation inputs, rather than falling
        # back to the later post-disposition Observation set.
        request["decision_work_package"] = json.loads(json.dumps(decision_work_package))

        supplied_decision = request.get("formal_decision")
        source_payload = dict(supplied_decision) if isinstance(supplied_decision, dict) else {}
        # BUSINESS_DECISION_SOURCE carries Formal ingress identity at the top level.\n        # Preserve it when projecting the internal source payload; otherwise the\n        # validator sees an empty formal_entry_source even though the durable BDS\n        # is correctly bound to the actual entry.\n        for identity_field in (\n            "formal_entry_source",\n            "entry_mode",\n            "task_id",\n            "task_run_id",\n            "occurrence_id",\n        ):\n            if identity_field in request:\n                source_payload.setdefault(identity_field, request.get(identity_field))\n        current_context = query_context.get("current") or {}
        source_payload.setdefault("request_id", str(request.get("request_id") or f"{parent_id}__business_decision_source"))
        source_payload.setdefault("parent_request_id", parent_id)
        source_payload.setdefault("decision_id", str(request.get("decision_id") or f"{parent_id}_decision"))
        source_payload.setdefault("consumed_snapshot", str(request.get("consumed_snapshot") or current_context.get("latest_snapshot") or ""))
        source_payload.setdefault("requested_at_beijing", parent_request.get("requested_at_beijing") or parent_request.get("request_time_beijing"))
        source_payload.setdefault("market_date", query_context.get("market_date") or parent_request.get("market_date"))
        source_payload.setdefault("data_as_of_beijing", query_context.get("generated_at_beijing") or source_payload.get("requested_at_beijing"))
        source_payload["request_type"] = BUSINESS_DECISION_SOURCE
        decision_response = request.get("decision_response")
        request_presentation = request.get("presentation_content")
        response_presentation = (
            decision_response.get("presentation_content")
            if isinstance(decision_response, dict)
            else None
        )
        supplied_presentations = [
            value for value in (request_presentation, response_presentation)
            if value not in (None, "")
        ]
        if supplied_presentations and len(set(str(value) for value in supplied_presentations)) != 1:
            raise ValueError("formal presentation content conflicts across request entry points")
        presentation_content = (
            response_presentation
            if response_presentation not in (None, "")
            else request_presentation
        )
        # A Business Decision Source without the frozen user-visible body is
        # not a valid Formal Decision completion input.  Do not publish a
        # half-complete decision event that notification_center cannot safely
        # consume.
        if presentation_content in (None, ""):
            raise ValueError(
                "business decision source requires frozen presentation_content"
            )
        validate_formal_presentation_provenance(
            str(presentation_content),
            parent_request_id=parent_id,
            decision_id=str(request.get("decision_id") or "").strip(),
            market_date=str(request.get("market_date") or parent_request.get("market_date") or "").strip(),
            root=ROOT,
        )
        if presentation_content not in (None, ""):
            source_payload["presentation_content"] = presentation_content
            binding = request.get("presentation_binding")
            source_payload["presentation_binding"] = (
                dict(binding) if isinstance(binding, dict) else {}
            )
            source_payload["presentation_binding"]["full_content"] = presentation_content
            nested = source_payload.get("decision_response")
            if not isinstance(nested, dict):
                nested = {}
            nested["presentation_content"] = presentation_content
            source_payload["decision_response"] = nested
            request["presentation_content"] = presentation_content
        if isinstance(supplied_decision, dict) and supplied_decision:
            # A pre-shaped formal_decision is only legitimate for an explicitly
            # selected historical forensic replay. Current production ingress
            # must always project actor answers through the request-bound DWP.
            # Otherwise numeric/positional answers can bypass the problem_id
            # coverage contract and still reach the canonical writer.
            if not forensic_historical_replay:
                raise ValueError(
                    "current Business Decision Source ingress must use "
                    "actor-only decision_response keyed by request-bound problem_id"
                )
            source_payload = source_payload
        else:
            if not isinstance(decision_response, dict):
                raise ValueError("business decision source requires actor-only structured decision_response")
            source_payload = project_decision_response(source_payload, decision_response, decision_work_package, forensic_historical_replay=forensic_historical_replay)
            # The projection builder returns a canonical source object, but
            # ingress identity belongs to the immutable request envelope. Keep
            # that identity bound across the same-run projection boundary;
            # otherwise the downstream source validator sees an anonymous
            # source even though the parent request carried a valid Formal
            # entry/source mapping.
            for identity_field in (
                "formal_entry_source",
                "entry_mode",
                "task_id",
                "task_run_id",
                "occurrence_id",
            ):
                if identity_field in request:
                    source_payload[identity_field] = request.get(identity_field)
            for canonical_field in (
                "opportunity_status",
                "continued_holding_opportunity_cost",
                "decision_evidence_consumption",
            ):
                if request.get(canonical_field) not in (None, "", [], {}):
                    source_payload[canonical_field] = request.get(canonical_field)
        # Resolve and validate the exact request-bound PIT snapshot before
        # projecting the Business Decision Source. This path previously used
        # snapshot_rel/snapshot without selecting them, so a valid manual
        # Formal Decision could fail with NameError before canonical writing.
        decision_time = str(
            source_payload.get("requested_at_beijing")
            or parent_request.get("requested_at_beijing")
            or parent_request.get("request_time_beijing")
            or ""
        ).strip()
        availability_time = str(
            query_context.get("generated_at_beijing")
            or source_payload.get("data_as_of_beijing")
            or decision_time
            or ""
        ).strip()
        snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(
            str(source_payload.get("market_date") or "").strip(),
            decision_time,
            availability_time=availability_time,
            consumed_snapshot=str(source_payload.get("consumed_snapshot") or "").strip(),
        )
        if not snapshot_rel:
            raise ValueError(
                "business decision source requires a legal request-bound PIT snapshot: "
                + pit_status
            )
        source_payload["request_bound_fact_binding"] = {
            "snapshot_id": snapshot_rel,
            "market_date": str(snapshot.get("market_date") or market_date or "").strip(),
            "market_phase": str(snapshot.get("market_phase") or "").strip(),
            "captured_at": str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "").strip(),
            "pit_status": pit_status,
        }
        source = validate_source(
            source_payload,
            expected_snapshot=str(request.get("consumed_snapshot") or source_payload.get("consumed_snapshot") or "").strip(),
            expected_market_date=str(snapshot.get("market_date") or market_date or "").strip(),
            expected_market_phase=str(snapshot.get("market_phase") or "").strip(),
            expected_captured_at=str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "").strip(),
        )
        # Bind the selected request-bound DWP only after Source identity and
        # fingerprinting.  It is validation context, not mutable Source
        # business content, so replay/idempotency fingerprints remain stable.
        source = freeze_source_work_package(source, decision_work_package)
        source_fingerprint_value = source["fingerprint"]
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("D_SOURCE_BINDING", obj=source, dwp=source.get("decision_work_package"), object_type="business_decision_source.bound", provenance=f"historical_query_context:{replay_dwp_commit}", source_fingerprint_value=source_fingerprint_value, historical=True)
        source_reply_ready = True
        request["_business_decision_source"] = source
        request["_source_fingerprint"] = source_fingerprint_value
        # Deterministic projection only: no new facts, ranking, or investment
        # judgment is introduced by this branch.
        projected = build_formal_completion_from_source(source)
        projected.pop("request_type", None)
        projected.pop("source_type", None)
        projected.pop("source_durable", None)
        projected.pop("reply_ready", None)
        projected.pop("projection_status", None)
        projected.pop("fingerprint", None)
        request["formal_decision"] = projected
    if original_source_dispatch:
        print(json.dumps({"ok": True, "request_id": request.get("request_id"), "business_decision_source_persisted": True, "completion_required": True}, ensure_ascii=False))
        return 0
    system_review_recorded, system_review_idempotent = record_scheduled_system_review(request)
    if system_review_recorded:
        result = {"ok": True, "request_id": request.get("request_id"), "system_review_completion": True, "idempotent_replay": system_review_idempotent, "canonical_publication": "events/reviews/system_" + str((request.get("presentation_binding") or {}).get("task_run_id") or "") + ".json"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if str(request.get("ingress_mode") or "").upper() == "HISTORICAL_BACKFILL":
        result = process_historical_backfill_request(request)
        result.update({
            "request_id": request.get("request_id"),
            "trade_event_recorded": True,
            "account_sync_status": "NOT_APPLICABLE",
            "canonical_ingress_state": CANONICAL_INGRESS_SUBMITTED,
            "canonical_ingress_failure_reason": "",
            "source_dispatch": source_dispatch,
            "source_durable": bool(source_dispatch),
            "source_immutable": bool(source_dispatch),
            "source_fingerprint": source_fingerprint_value,
            "reply_ready": source_reply_ready,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    canonical_ingress_contract = canonical_ingress_contract_for_request(request)
    trades = _request_trade_events(request)
    prior_account = load_json(ACCOUNT) if ACCOUNT.exists() else {}
    supplied_account = request.get("account_fact")
    existing_trades = []
    for trade in trades:
        confirmed_at = (
            trade.get("confirmed_at_beijing") or request.get("requested_at_beijing")
            or trade.get("executed_at") or prior_account.get("updated_at")
        )
        replay_key = _trade_idempotency_key(trade, confirmed_at)
        existing = _find_existing_trade(trade, confirmed_at, replay_key)
        existing_trades.append(existing)
        if request.get("account_fact") is None and existing is None:
            supplied_account = _apply_trade_to_account(supplied_account or prior_account, trade)
    account_sync_status = "NOT_APPLICABLE"
    if is_broker_screenshot_request(request) and not isinstance(supplied_account, dict) and not trades:
        result = {
            "ok": False,
            "request_id": request.get("request_id"),
            "interaction_scenario": request.get("interaction_scenario"),
            "status": "NO_ACCOUNT_FACT",
            "account_sync_status": "ACCOUNT_SYNC_NOT_PERFORMED",
            "canonical_ingress_state": canonical_ingress_contract["terminal_state"],
            "canonical_ingress_failure_reason": canonical_ingress_contract["reason"],
            "dashboard_updated": False,
            "detail": "Broker screenshot request has no request-scoped account_fact; market success must not be treated as account sync success.",
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1
    if (
        canonical_ingress_contract["required"]
        and canonical_ingress_contract["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
        and canonical_ingress_contract["reason"] != "missing_account_fact"
    ):
        print(json.dumps({
            "ok": False,
            "status": "CANONICAL_INGRESS_FAILED_EXPLICITLY",
            "canonical_ingress_state": canonical_ingress_contract["terminal_state"],
            "canonical_ingress_failure_reason": canonical_ingress_contract["reason"],
            "request": str(req_path.relative_to(ROOT)).replace("\\", "/"),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    broker_shape_error = validate_broker_account_fact_ingress(request, supplied_account)
    if broker_shape_error:
        print(json.dumps({
            "ok": False,
            "request_id": request.get("request_id"),
            "status": "BROKER_ACCOUNT_FACT_SCHEMA_INVALID",
            "account_sync_status": "ACCOUNT_SYNC_NOT_PERFORMED",
            "canonical_ingress_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "canonical_ingress_failure_reason": "broker_account_fact_schema_invalid",
            "dashboard_updated": False,
            "detail": broker_shape_error,
        }, ensure_ascii=False))
        return 1

    if isinstance(supplied_account, dict):
        supplied_account = merge_account_fact(prior_account, supplied_account)
        supplied_account.setdefault("status", "VALID")
        supplied_account.setdefault("validity_mode", "EVENT_DRIVEN_CARRY_FORWARD")
        if account_fact_is_older(prior_account, supplied_account):
            account_sync_status = "STALE_ACCOUNT_FACT_IGNORED"
        else:
            # Use the canonicalized event history returned by merge_account_fact:
            # it may have upgraded a previously unresolved IPO registration.
            prior_events = supplied_account.get("account_change_events_after_confirmed_at") or []
            new_events = _account_change_events(prior_account, supplied_account, request, trades)
            known = {str(x.get("idempotency_key") or x.get("event_id") or "") for x in prior_events}
            supplied_account["account_change_events_after_confirmed_at"] = prior_events + [x for x in new_events if str(x.get("idempotency_key")) not in known]
            if supplied_account == prior_account:
                account_sync_status = "ACCOUNT_SYNC_IDEMPOTENT_NOOP"
            else:
                account_sync_status = "ACCOUNT_FACT_UPDATED"
                atomic_json_write(ACCOUNT, supplied_account)
    account = load_json(ACCOUNT)
    if account.get("status") != "VALID":
        raise RuntimeError("account_fact is not VALID")
    decision_recorded, decision_id = record_formal_decision(request)
    formal_decision_current = False
    if decision_recorded:
        formal_decision_current, formal_action_changed = project_current_formal_action(
            account, request, decision_id
        )
        if formal_action_changed:
            atomic_json_write(ACCOUNT, account)
    trade_event_recorded = False
    persisted_trade_events = []
    for trade_index, trade in enumerate(trades):
        confirmed_at = trade.get("confirmed_at_beijing") or request.get("requested_at_beijing") or trade.get("executed_at") or account.get("updated_at")
        idempotency_key = _trade_idempotency_key(trade, confirmed_at)
        existing = existing_trades[trade_index] or _find_existing_trade(trade, confirmed_at, idempotency_key)
        if existing:
            event, event_id, trade_event_recorded = existing, str(existing.get("event_id") or ""), True
            # A request-scoped replay may repair an earlier unlinked trade, but
            # never by guessing from the latest account action.  The replay must
            # carry an explicit decision identity or a same-request formal decision.
            explicit_replay_decision_id = str(trade.get("linked_decision_id") or trade.get("decision_id") or decision_id or "").strip()
            if not str(event.get("linked_decision_id") or "").strip() and explicit_replay_decision_id:
                event["linked_decision_id"] = explicit_replay_decision_id
            # A request-scoped confirmation time is authoritative for an
            # idempotent replay.  Never replace an earlier PIT confirmation with
            # the current account snapshot's updated_at.
            changed = False
            requested_confirmed_at = str(trade.get("confirmed_at_beijing") or request.get("requested_at_beijing") or trade.get("executed_at") or "").strip()
            if requested_confirmed_at and str(event.get("confirmed_at_beijing") or "") != requested_confirmed_at:
                event["confirmed_at_beijing"] = requested_confirmed_at
                event["execution_date"] = trade.get("market_date") or requested_confirmed_at[:10]
                event["confirmation_date"] = trade.get("market_date") or requested_confirmed_at[:10]
                event["idempotency_key"] = _trade_idempotency_key(trade, requested_confirmed_at)
                changed = True
            # Idempotent fee/account replays must also converge stale linkage and
            # attribution written by an older producer; they never create another event.
            linked_decision_id = resolve_trade_linked_decision_id(
                trade,
                decision_id,
                existing_linked_decision_id=str(event.get("linked_decision_id") or ""),
            )
            if not str(event.get("linked_decision_id") or "").strip() and linked_decision_id:
                event["linked_decision_id"] = linked_decision_id
                changed = True
            refreshed_attribution = execution_attribution(event, linked_decision_id)
            refreshed_hypothesis = refreshed_attribution.get("hypothesis_id")
            if (
                event.get("execution_attribution") != refreshed_attribution
                or event.get("hypothesis_id") != refreshed_hypothesis
            ):
                event["execution_attribution"] = refreshed_attribution
                event["hypothesis_id"] = refreshed_hypothesis
                changed = True
            if changed:
                atomic_json_write(ROOT / "events" / "trades" / f"{event_id}.json", event)
        else:
            event_id = _trade_event_id(request, trade, idempotency_key, trade_index, len(trades))
            linked_decision_id = resolve_trade_linked_decision_id(trade, decision_id)
            attribution = execution_attribution({**trade, "confirmed_at_beijing": confirmed_at}, linked_decision_id)
            event = {"event_id": event_id, "idempotency_key": idempotency_key, "confirmed_at_beijing": confirmed_at, "execution_date": trade.get("execution_date") or str(confirmed_at)[:10], "confirmation_date": trade.get("confirmation_date") or str(confirmed_at)[:10], "notification_id": trade.get("notification_id"), "name": trade.get("name"), "code": trade.get("code"), "side": trade.get("side"), "quantity": trade.get("quantity"), "price": trade.get("price"), "amount": trade.get("amount"), "lifecycle": trade.get("lifecycle"), "source": trade.get("source", account.get("source")), "source_confidence": trade.get("source_confidence"), "linked_decision_id": linked_decision_id or None, "hypothesis_id": trade.get("hypothesis_id") or attribution.get("hypothesis_id") or None, "execution_status": "EXECUTED", "execution_attribution": attribution}
            event_path = ROOT / "events" / "trades" / f"{event_id}.json"
            event_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_write(event_path, event)
            trade_event_recorded = True
        # Always reconcile event-backed human-readable records, including idempotent replay.
        # This repairs a missing archive/CASE line without creating a second trade event.
        archive_line = f"- {event['confirmed_at_beijing']}：{event.get('name')}（{event.get('code')}）{event.get('side')} {int(event.get('quantity') or 0):,}份/股，成交价{event.get('price')}，成交本金{float(event.get('amount') or 0):,.2f}元；来源：{event.get('source')}。"
        case_line = f"- 待复盘CASE｜{event['confirmed_at_beijing']}｜{event.get('name')}（{event.get('code')}）｜{event.get('side')} {int(event.get('quantity') or 0):,}份/股｜生命周期：{event.get('lifecycle') or '待确认'}｜仅登记真实成交，复盘结论留待盘后形成。"
        upsert_formal_line(ROOT, ARCHIVE.name, TRADE_START, TRADE_END, event_id, archive_line, before_heading="## 6. 历史Excel与专项数据来源")
        upsert_formal_line(ROOT, EXPERIENCE.name, CASE_START, CASE_END, event_id, case_line, before_heading="## 3. 历史研究与专项回测")
        sync_experience_transaction_index(event)
        linked_event_decision_id = str(event.get("linked_decision_id") or "").strip()
        prior_formal_action = account.get("formal_action") or {}
        if linked_event_decision_id:
            decision_path = ROOT / "events" / "decisions" / f"{linked_event_decision_id}.json"
            linked_decision = load_json(decision_path) if decision_path.exists() else {}
            linked_formal = linked_decision.get("formal_decision") if isinstance(linked_decision.get("formal_decision"), dict) else {}
            account["formal_action"] = {
                **prior_formal_action,
                "action": linked_formal.get("action") or linked_formal.get("amount_action") or prior_formal_action.get("action") or "",
                "quantity": linked_formal.get("quantity", prior_formal_action.get("quantity")),
                "decision_id": linked_event_decision_id,
                "decision_time": linked_formal.get("decision_time") or linked_formal.get("data_as_of_beijing") or linked_decision.get("decision_time_beijing") or prior_formal_action.get("decision_time"),
                "source": "CHATGPT_FORMAL_DECISION",
                "lifecycle": linked_formal.get("lifecycle") or prior_formal_action.get("lifecycle"),
                "applicable_object": linked_formal.get("candidate_code") or linked_formal.get("code") or linked_decision.get("candidate_code") or prior_formal_action.get("applicable_object") or "",
                "validity": "ACTIVE",
                "execution_status": "EXECUTED",
                "execution_fact_ref": f"events/trades/{event_id}.json",
                "last_executed_event_id": event_id,
            }
        else:
            account["formal_action"] = {
                **prior_formal_action,
                "execution_status": "EXECUTED",
                "execution_fact_ref": f"events/trades/{event_id}.json",
                "last_executed_event_id": event_id,
            }
        atomic_json_write(ACCOUNT, account)
        write_trade_review_required(event)
        persisted_trade_events.append((trade, event))

    # Refresh the CURRENT trade mirror only after this request's trade events
    # are durable.  Doing this before the loop leaves CURRENT and downstream
    # trigger construction one confirmed trade behind.
    latest_trade_event_id = _latest_trade_event_id()
    if latest_trade_event_id:
        current_path = ROOT / "data/state/CURRENT.json"
        current = load_json(current_path) if current_path.exists() else {}
        if current.get("last_trade_event_id") != latest_trade_event_id:
            current["last_trade_event_id"] = latest_trade_event_id
            current["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            atomic_json_write(current_path, current)

    sync_current_account_mirror(ROOT, account)
    review_recorded, review_idempotent = record_post_close_review(account, request)
    unavailable_recorded, unavailable_idempotent = (False, False)
    for trade, event in persisted_trade_events:
        recorded, idempotent = record_unrecoverable_review_prerequisite(account, request, event)
        unavailable_recorded = unavailable_recorded or recorded
        unavailable_idempotent = unavailable_idempotent or idempotent
    # Render after event/review persistence so a newly confirmed execution is
    # visible in the same canonical dashboard update, rather than one request
    # behind the machine facts.
    dashboard = replace_block(DASHBOARD.read_text(encoding="utf-8"), START, END, build_dashboard_block(account, latest_canonical_formal_decision(ROOT) or request.get("formal_decision"), request), insert_after_heading=True)
    write_formal_text_if_changed(ROOT, DASHBOARD.name, dashboard)
    # Keep the three human-readable fact documents synchronized even when the
    # request only confirms a fee/account snapshot and creates no new trade event.
    # Actual holdings are mandatory continuous-monitor objects.  Project account
    # membership on the same canonical ingress that confirms an account/trade
    # change; do not wait for a later formal decision to repair market coverage.
    account_membership_changed = account_sync_status == "ACCOUNT_FACT_UPDATED" or bool(trade_event_recorded)
    observation_universe_changed = persist_monitor_universe(
        ROOT, account, (request.get("formal_decision") or {}) if formal_decision_current else {}
    ) if (formal_decision_current or account_membership_changed) else False
    formal_files_sync = sync_formal_files(ROOT, account)
    result = {"ok": True, "request_id": request.get("request_id"), "interaction_scenario": request.get("interaction_scenario"), "account_updated_at": account.get("updated_at"), "dashboard_updated": True, "formal_decision_recorded": decision_recorded, "formal_decision_id": decision_id, "trade_event_recorded": trade_event_recorded, "post_close_review_recorded": review_recorded, "post_close_review_idempotent_noop": review_idempotent, "review_prerequisite_unavailable_recorded": unavailable_recorded, "review_prerequisite_unavailable_idempotent_noop": unavailable_idempotent, "formal_files_sync": formal_files_sync, "observation_universe_changed": observation_universe_changed, "account_sync_status": account_sync_status, "canonical_ingress_state": canonical_ingress_contract["terminal_state"], "canonical_ingress_failure_reason": None if canonical_ingress_contract["terminal_state"] == CANONICAL_INGRESS_SUBMITTED else canonical_ingress_contract["reason"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
