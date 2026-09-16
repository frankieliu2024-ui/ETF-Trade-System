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
from sync_formal_files import sync_formal_files, latest_canonical_formal_decision, format_position_pnl
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
        title = "最近一次正式收盘复盘" if scenario == "POST_CLOSE_REVIEW" else "最近一次正式盘中决策"
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
    review_error = validate_managed_position_review_contract(
        decision.get("managed_position_reviews"), account_for_lifecycle,
        "formal_decision.managed_position_reviews",
    )
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
    request_id = str(request.get("request_id") or "").strip()
    is_manual_completion = str(request.get("source") or "").strip() == "CHATGPT_MANUAL_FORMAL_COMPLETION"
    parent_request_id = str(request.get("parent_request_id") or "").strip()
    if is_manual_completion and (not parent_request_id or not request_id or parent_request_id == request_id):
        raise ValueError("manual formal completion requires distinct envelope and parent request identities")
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
        "recorded_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
    event_path = ROOT / "events/decisions" / f"{decision_id}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    if event_path.exists():
        prior = load_json(event_path)
        if is_manual_completion and prior.get("fingerprint") == fingerprint:
            if (
                prior.get("price_source_snapshot") == snapshot_rel
                and prior.get("point_in_time_status") == pit_status
            ):
                return True, decision_id
            raise ValueError("manual formal decision retry changed its PIT/source snapshot linkage")
        if is_manual_completion:
            raise ValueError("manual formal decision_id is already bound to a different request or decision")
        if prior.get("fingerprint") == fingerprint and prior.get("price_source_snapshot") == snapshot_rel:
            return True, decision_id
    atomic_json_write(event_path, event)
    return True, decision_id


def validate_formal_decision_contract(decision: dict) -> str:
    """Validate stable user-facing decision enums before persistence.

    Formal facts are validated at their canonical ingress, rather than repaired
    later by notification rendering.  This intentionally validates only the
    existing contract fields and does not infer a transaction or alter MASTER.
    """
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
    """Validate lifecycle actions without narrowing the existing multi-object schema.

    Current formal decisions use both a single legacy string and a mapping from
    security name to per-object lifecycle text.  The canonical contract is the
    action expressed for each object; explanatory text may accompany that
    action and is retained for compatibility.
    """
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
        # Historical Trial/Confirm/exit wording is explanatory only.  It may
        # follow a current action, but it cannot be the current action itself.
        historical_exit = "已退出" in clause
        if "持有并" in clause or "持有且" in clause:
            return f"{object_name} clause must state the current holding action explicitly: {clause}"
        current_actions = ("持有管理", "持仓管理", "降低风险", "退出")
        has_current_action = any(
            action in clause and not (action == "退出" and historical_exit)
            for action in current_actions
        )
        legacy_hold = "持有" in clause and not has_current_action and "持有并" not in clause and "持有且" not in clause
        if not has_current_action and current_seen and (historical_exit or any(term in clause for term in ("Trial", "Confirm", "观察", "已关闭", "继续"))):
            continue
        if not has_current_action and not legacy_hold:
            return f"{object_name} clause has no registered lifecycle action: {clause}"
        current_seen = True
    return ""


def _load_account_for_lifecycle_validation() -> dict:
    """Load the canonical account fact for current lifecycle validation."""
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


def validate_managed_position_lifecycle(value: object, account: dict, object_name: str = "lifecycle") -> str:
    """Require one explicit lifecycle line/entry for every positive account position."""
    managed = build_managed_position_projection(ROOT, account or {}).get("positions", [])
    if not managed:
        return ""
    covered: set[str] = set()
    if isinstance(value, dict):
        for key in value:
            code = _managed_position_code(key, managed)
            if code:
                covered.add(code)
    elif isinstance(value, str):
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        for line in lines:
            matches = [item["code"] for item in managed if item["code"] in line]
            if len(matches) == 1:
                covered.add(matches[0])
            elif len(matches) > 1:
                return f"{object_name} must provide one separate line per managed position"
    missing = [f"{item['name']}（{item['code']}）" for item in managed if item["code"] not in covered]
    if missing:
        return f"{object_name} is missing current managed positions: {', '.join(missing)}"
    return ""


def validate_managed_position_review_contract(value: object, account: dict, object_name: str = "managed_position_reviews") -> str:
    """Validate decision-supplied, per-position review completeness only.

    This is deliberately not an action engine: it checks object coverage and
    required evidence fields, while leaving every action and conclusion to the
    Formal Decision actor.
    """
    managed = build_managed_position_projection(ROOT, account or {}).get("positions", [])
    if not managed:
        return ""
    if not isinstance(value, list) or not value:
        return f"{object_name} must be a non-empty list with one entry per managed position"
    by_code = {item["code"]: item for item in managed}
    covered: set[str] = set()
    for index, review in enumerate(value):
        if not isinstance(review, dict):
            return f"{object_name}[{index}] must be an object"
        code = normalize_code(review.get("security_code") or review.get("code") or "")
        if code not in by_code:
            return f"{object_name}[{index}] has unknown managed position"
        if code in covered:
            return f"{object_name} must cover each managed position exactly once"
        covered.add(code)
        required = (
            "current_action", "holding_state_risk_reward_evidence",
            "capital_use", "action_changes_now", "next_change_condition",
        )
        missing = [key for key in required if key not in review or review[key] in (None, "", [])]
        if missing:
            return f"{object_name}[{index}] missing required fields: {', '.join(missing)}"
        capital_use = review["capital_use"]
        if not isinstance(capital_use, dict) or not capital_use.get("continued_holding_vs_cash"):
            return f"{object_name}[{index}].capital_use requires continued_holding_vs_cash"
        action = str(review["current_action"]).strip()
        if action in {"降低风险", "退出"}:
            if review.get("action_changes_now") is not True:
                return f"{object_name}[{index}] reduce/exit must explicitly change action now"
            if not review.get("action_detail") or not review.get("capital_destination"):
                return f"{object_name}[{index}] reduce/exit requires action_detail and capital_destination"
        elif not isinstance(review["action_changes_now"], bool):
            return f"{object_name}[{index}].action_changes_now must be boolean"
    missing = [f"{item['name']}（{item['code']}）" for item in managed if item["code"] not in covered]
    if missing:
        return f"{object_name} is missing current managed positions: {', '.join(missing)}"
    return ""


def _managed_lifecycle_lines(value: object, account: dict) -> list[str]:
    managed = build_managed_position_projection(ROOT, account or {}).get("positions", [])
    if not managed:
        return []
    actions: dict[str, str] = {}
    if isinstance(value, dict):
        for key, action in value.items():
            code = _managed_position_code(key, managed)
            if code:
                actions[code] = str(action or "").strip()
    elif isinstance(value, str):
        for line in value.splitlines():
            matches = [item["code"] for item in managed if item["code"] in line]
            if len(matches) == 1:
                code = matches[0]
                actions[code] = line.split("：", 1)[1].strip() if "：" in line else line
    return [f"- {item['name']}（{item['code']}）：{actions.get(item['code'], '事实不足：未提供本轮持仓动作')}" for item in managed]


def validate_current_lifecycle_contract(value: object, account: dict, object_name: str = "lifecycle") -> str:
    """Validate current lifecycle semantics against the canonical account fact.

    Formal decisions and post-close reviews share this ingress contract.  A
    historical Trial/Confirm mention remains explanatory when explicitly
    marked as such, but it cannot be the current lifecycle of a held asset.
    """
    if not isinstance(value, dict):
        return ""
    membership = active_account_asset_codes(ROOT, account or {})
    held_codes = membership["etf"] | membership["stocks"]
    position_names = {
        normalize_code(first(position, "code", "symbol", "security_code", "instrument_code")): str(
            first(position, "name", "security_name", "instrument_name") or ""
        )
        for position in (account or {}).get("positions") or []
        if isinstance(position, dict)
    }
    opportunity_terms = ("观察", "Trial", "Confirm", "观察机会", "Trial机会", "Confirm机会")
    holding_terms = ("持有管理", "持仓管理", "降低风险", "退出", "持有")
    for security, action in value.items():
        text = str(action or "").strip()
        code = _lifecycle_object_code(security)
        if not code:
            security_text = str(security or "")
            code = next((candidate for candidate, name in position_names.items() if name and name in security_text), "")
        is_held = code in held_codes
        historical = _is_historical_lifecycle_explanation(text) or (
            "继续" in text and any(term in text for term in ("持有管理", "持仓管理", "降低风险", "退出"))
        )
        if is_held and any(term in text for term in opportunity_terms) and not historical:
            return f"{object_name}[{security}] uses opportunity lifecycle for a held asset; current action must be holding management"
        if not is_held and any(term in text for term in holding_terms) and not historical:
            return f"{object_name}[{security}] uses holding lifecycle for an unheld asset; current action must be observation or opportunity"
    return ""


def record_unrecoverable_review_prerequisite(account: dict, request: dict, trade_event: dict) -> tuple[bool, bool]:
    """Project an evidence-backed terminal review state, never a normal CASE."""
    assessment = request.get("historical_recovery_assessment")
    if request.get("interaction_scenario") != "POST_CLOSE_REVIEW" or not assessment:
        return False, False
    event_id = str(trade_event.get("event_id") or "")
    ok, reason = validate_unrecoverable_assessment(assessment, event_id)
    if not ok:
        raise ValueError(f"invalid unrecoverable review prerequisite: {reason}")
    market_date = str(assessment["review_target_market_date"])
    event_path = ROOT / "events" / "reviews" / f"{market_date}.json"
    prior = load_json(event_path) if event_path.exists() else {}
    if prior.get("event_type") == "FORMAL_POST_CLOSE_REVIEW":
        raise RuntimeError("normal formal review already exists; cannot overwrite with terminal state")
    event = build_unrecoverable_review_event(
        assessment,
        request_id=str(request.get("request_id") or ""),
        created_at_beijing=datetime.now(SHANGHAI).isoformat(timespec="seconds"),
    )
    if prior == event:
        return True, True
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(event_path, event)
    upsert_formal_line(
        ROOT,
        EXPERIENCE.name,
        REVIEW_UNAVAILABLE_START,
        REVIEW_UNAVAILABLE_END,
        event_id,
        terminal_experience_line(assessment),
        before_heading="## 6. 版本维护记录",
    )
    return True, False


def sync_experience_case_mapping_index(review: dict) -> None:
    """Project canonical review trade→CASE mappings into the transaction index."""
    # Review mappings are the canonical owner; the transaction index is only a human projection.
    mappings = []

    def collect(value):
        if isinstance(value, dict):
            if value.get("trade_event_id") and value.get("case_id"):
                mappings.append((str(value["trade_event_id"]), str(value["case_id"])))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(review.get("case_mapping") or {})
    if not mappings or not EXPERIENCE.exists():
        return
    text = EXPERIENCE.read_text(encoding="utf-8")
    lines = text.splitlines()
    changed = False
    for index, line in enumerate(lines):
        if not line.startswith("|2026-") or "TRADE_EVENT:" not in line:
            continue
        for event_id, case_id in mappings:
            if f"TRADE_EVENT:{event_id}" not in line:
                continue
            parts = line.strip().strip("|").split("|")
            if len(parts) < 10:
                continue
            marker = f"<!-- TRADE_EVENT:{event_id} -->"
            # Rebuild the matched row from its table fields and append one
            # canonical marker. This repairs historical duplicate markers while
            # keeping the transaction identity and unrelated rows unchanged.
            without_markers = line.replace(marker, "").strip()
            if without_markers.startswith("|"):
                without_markers = without_markers[1:]
            normalized_parts = without_markers.split("|")
            if len(normalized_parts) < 10:
                continue
            remark = normalized_parts[9].strip()
            if case_id not in remark:
                normalized_parts[9] = f"{case_id}；{remark}" if remark else case_id
            normalized_line = "|" + "|".join(normalized_parts) + f" {marker}"
            if normalized_line != line:
                lines[index] = normalized_line
                changed = True
            break
    if changed:
        newline = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        write_formal_text_if_changed(ROOT, EXPERIENCE.name, newline)



def _purge_case_mapping_rows() -> None:
    """Remove legacy routing rows from the human CASE managed block."""
    text = EXPERIENCE.read_text(encoding="utf-8")
    start, end = text.find(CASE_DETAILS_START), text.find(CASE_DETAILS_END)
    if start < 0 or end < start:
        return
    body_start = start + len(CASE_DETAILS_START)
    block = text[body_start:end]
    kept = []
    for line in block.splitlines():
        stripped = line.strip()
        if (
            re.match(r"^\\d{8}[_-]", stripped)
            or stripped.startswith("2026-") and "｜" in stripped
            or "TRADE_EVENT:" in stripped
            or "已归入CASE" in stripped
            or "待复盘CASE" in stripped
        ):
            continue
        kept.append(line)
    cleaned = "\n".join(kept).strip("\n")
    updated = text[:body_start] + "\n" + cleaned + "\n" + text[end:]
    write_formal_text_if_changed(ROOT, EXPERIENCE.name, updated)



_CASE_TEMPLATE_FIELDS = (
    "背景／生命周期",
    "关键证据",
    "执行",
    "结果",
    "判断质量",
    "执行质量",
    "风险收益质量",
    "资本使用效率",
    "最终结果／反事实",
    "经验",
)


def _case_detail_projection_entry(case_entry: str, case_id: str) -> str:
    """Build the shared human CASE template without manufacturing absent facts."""
    text = EXPERIENCE.read_text(encoding="utf-8")
    case_match = re.search(
        rf"^### (2\.\d+) {re.escape(case_id)}[:：]",
        text,
        re.MULTILINE,
    )
    if case_match:
        ordinal = int(case_match.group(1).split(".")[1])
    else:
        ordinals = [
            int(value)
            for value in re.findall(r"^### 2\.(\d+) CASE-", text, re.MULTILINE)
        ]
        ordinal = max(ordinals, default=2) + 1
    body = case_entry.strip()
    body_lines = body.splitlines()
    # CASE_MAPPING rows are audit metadata, never CASE_REVIEW prose.
    body_lines = [
        line for line in body_lines
        if not re.match(r"^\d{8}[_-].*｜- 已归入CASE-", line.strip())
    ]
    body = "\n".join(body_lines).strip()
    body = re.sub(r"^### (?:2\.\d+ )?", "", body, count=1)
    if body.startswith(case_id):
        body = re.sub(rf"^{re.escape(case_id)}[：:]?\s*", "", body, count=1)
    title = body.splitlines()[0].split("｜", 1)[0].strip() if body else "CASE复盘"
    title = title or "CASE复盘"

    fields: dict[str, list[str]] = {name: [] for name in _CASE_TEMPLATE_FIELDS}
    current_field = ""
    unstructured: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^-\s*(背景／生命周期|关键证据|执行|结果|判断质量|执行质量|风险收益质量|资本使用效率|最终结果／反事实|经验)：\s*(.*)$", line.strip())
        if match:
            current_field = match.group(1)
            if match.group(2):
                fields[current_field].append(match.group(2))
        elif current_field and line.startswith(("  ", "\t")):
            fields[current_field].append(line.strip())
        elif line.strip():
            unstructured.append(line.strip())
    if unstructured and not any(fields.values()):
        fields["背景／生命周期"].append("；".join(unstructured))
    rendered = [f"### 2.{ordinal} {case_id}：{title}"]
    for name in _CASE_TEMPLATE_FIELDS:
        value = "\n".join(fields[name]).strip() or "信息不足（现有正式复盘未记录该字段）"
        rendered.append(f"- {name}：{value}")
    return "\n".join(rendered)


def _case_lifecycle_update_text(root: Path, case_id: str, review: dict, updates: list[dict]) -> str:
    market_date = str(review.get("market_date") or "")
    status = next((str(item.get("case_status") or "").strip() for item in reversed(updates) if item.get("case_status")), "")
    reason = next((str(item.get("mapping_reason") or "").strip() for item in reversed(updates) if item.get("mapping_reason")), "")
    trade_lines = []
    for item in updates:
        event_id = str(item.get("trade_event_id") or "").strip()
        if not event_id:
            continue
        trade_path = root / "events" / "trades" / f"{event_id}.json"
        if not trade_path.exists():
            if str(item.get("case_status") or "").upper() == "RESOLVED":
                raise ValueError(f"resolved CASE update lacks canonical trade fact: {event_id}")
            continue
        trade = load_json(trade_path)
        if str(trade.get("code") or "") != str(item.get("security_code") or trade.get("code") or ""):
            raise ValueError(f"CASE update trade identity mismatch: {event_id}")
        if str(trade.get("execution_status") or "").upper() != "EXECUTED":
            raise ValueError(f"CASE update trade is not confirmed executed: {event_id}")
        side = str(trade.get("side") or "").upper()
        if str(item.get("case_status") or "").upper() == "RESOLVED" and side != "SELL":
            continue
        trade_lines.append(
            f"{trade.get('confirmed_at_beijing') or trade.get('executed_at_beijing') or '时间未记录'} "
            f"{side or '交易'} {trade.get('quantity', '数量未记录')}份@{trade.get('price', '价格未记录')}元 "
            f"(trade_event_id={event_id})"
        )
    if status.upper() == "RESOLVED" and not trade_lines:
        raise ValueError(f"resolved CASE update has no canonical executed SELL fact: {case_id}")
    parts = [f"状态={status or '未提供'}"]
    if trade_lines:
        parts.append("正式成交=" + "；".join(trade_lines))
    if reason:
        parts.append("复盘映射说明=" + reason)
    return f"- 后续正式复盘（{market_date}）：" + "；".join(parts)


def _append_case_update_field(entry: str, field: str, market_date: str, update_line: str) -> str:
    marker = f"后续正式复盘（{market_date}）"
    lines = entry.splitlines()
    field_prefix = f"- {field}："
    index = next((i for i, line in enumerate(lines) if line.startswith(field_prefix)), None)
    if index is None:
        raise ValueError(f"CASE template missing required field: {field}")
    section_end = next(
        (i for i in range(index + 1, len(lines)) if any(lines[i].startswith(f"- {name}：") for name in _CASE_TEMPLATE_FIELDS)),
        len(lines),
    )
    field_lines = lines[index + 1:section_end]
    update_pattern = re.compile(r"^\s*- 后续正式复盘（(\d{4}-\d{2}-\d{2})）：")
    other_lines = [line for line in field_lines if not update_pattern.match(line)]
    updates_by_date = {}
    for line in field_lines:
        match = update_pattern.match(line)
        if match and marker not in line:
            updates_by_date[match.group(1)] = line
    updates_by_date[market_date] = "  " + update_line
    updates = [updates_by_date[date] for date in sorted(updates_by_date)]
    lines[index + 1:section_end] = other_lines + updates
    return "\n".join(lines)


def _persist_existing_case_detail_projections(review: dict) -> None:
    """Project later canonical CASE lifecycle facts through the formal gateway."""
    if not EXPERIENCE.exists():
        return
    mapping = review.get("case_mapping") if isinstance(review.get("case_mapping"), dict) else {}
    updates = mapping.get("existing_case_updates") if isinstance(mapping.get("existing_case_updates"), list) else []
    grouped: dict[str, list[dict]] = {}
    for item in updates:
        if not isinstance(item, dict) or not str(item.get("case_id") or "").strip():
            continue
        if not str(item.get("case_status") or "").strip():
            continue
        grouped.setdefault(str(item["case_id"]).strip(), []).append(item)
    if not grouped:
        return
    text = EXPERIENCE.read_text(encoding="utf-8")
    start = text.find(CASE_DETAILS_START)
    end = text.find(CASE_DETAILS_END, start + len(CASE_DETAILS_START)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError("canonical CASE detail managed block is missing")
    block = text[start + len(CASE_DETAILS_START):end]
    for case_id, case_updates in grouped.items():
        match = re.search(rf"^### 2\.(\d+) {re.escape(case_id)}[:：].*$", block, re.MULTILINE)
        if not match:
            raise ValueError(f"canonical existing CASE detail not found: {case_id}")
        next_heading = re.search(r"^### ", block[match.end():], re.MULTILINE)
        section_end = match.end() + next_heading.start() if next_heading else len(block)
        section_start = match.start()
        section = block[section_start:section_end].strip()
        if not all(re.search(rf"^- {re.escape(field)}：", section, re.MULTILINE) for field in _CASE_TEMPLATE_FIELDS):
            section = _case_detail_projection_entry(section, case_id)
        update_line = _case_lifecycle_update_text(ROOT, case_id, review, case_updates)
        market_date = str(review.get("market_date") or "")
        for field in ("背景／生命周期", "结果", "最终结果／反事实"):
            section = _append_case_update_field(section, field, market_date, update_line)
        block = block[:section_start] + "\n" + section + "\n" + block[section_end:]
    replace_formal_block(ROOT, EXPERIENCE.name, CASE_DETAILS_START, CASE_DETAILS_END, block.strip())


def _normalize_executed_trade_case_mapping(review: dict, market_date: str) -> dict:
    """Normalize mapping-required executed trades at the formal review owner.

    Existing CASE ownership is recovered from prior canonical reviews by exact
    security code. A first-seen executed exit without a lawful CASE is recorded
    as an explicit canonical ineligibility, never as a fabricated CASE.
    """
    normalized = json.loads(json.dumps(review))
    mapping = normalized.setdefault("case_mapping", {})
    existing = mapping.setdefault("existing_case_updates", [])
    ineligible = mapping.setdefault("ineligible_executed_trades", [])
    mapped_ids = {str(x.get("trade_event_id") or "") for x in existing if isinstance(x, dict)}
    ineligible_ids = {str(x.get("trade_event_id") or "") for x in ineligible if isinstance(x, dict)}
    prior_cases = {}
    review_dir = ROOT / "events" / "reviews"
    for path in sorted(review_dir.glob("*.json")) if review_dir.exists() else []:
        try:
            payload = load_json(path)
        except (OSError, ValueError, TypeError):
            continue
        def walk(value):
            if isinstance(value, dict):
                code = str(value.get("security_code") or "")
                case_id = str(value.get("case_id") or "")
                if code and case_id:
                    prior_cases.setdefault(code, (case_id, str(value.get("case_status") or "RESOLVED")))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(payload)
    trade_dir = ROOT / "events" / "trades"
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        try:
            trade = load_json(path)
        except (OSError, ValueError, TypeError):
            continue
        if str(trade.get("execution_status") or "").upper() != "EXECUTED":
            continue
        event_id = str(trade.get("event_id") or "")
        trade_date = str(trade.get("confirmed_at_beijing") or trade.get("executed_at_beijing") or "")[:10]
        if not event_id or trade_date != market_date or event_id in mapped_ids or event_id in ineligible_ids:
            continue
        code = str(trade.get("code") or "")
        case = prior_cases.get(code)
        if case:
            existing.append({
                "trade_event_id": event_id,
                "decision_id": str(trade.get("linked_decision_id") or ""),
                "case_id": case[0],
                "security_code": code,
                "case_status": "RESOLVED",
                "mapping_reason": "正式复盘后将当前executed exit映射至该证券既有CASE；使用authoritative trade event身份。",
            })
            mapped_ids.add(event_id)
        else:
            ineligible.append({
                "trade_event_id": event_id,
                "decision_id": str(trade.get("linked_decision_id") or ""),
                "security_code": code,
                "eligibility": "EXPLICIT_CANONICAL_INELIGIBILITY",
                "reason": "正式复盘已完成，但当前不存在该证券既有CASE，且本次退出不满足既有通用CASE intake语义；不创建新CASE。",
            })
            ineligible_ids.add(event_id)
    return normalized

def _persist_post_close_review_projections(account: dict, request: dict, review: dict, event: dict) -> None:
    """Complete all review projections for first write and safe idempotent replay."""
    market_date = str(event.get("market_date") or review.get("market_date") or "")
    if not market_date:
        return
    archive_entry = str(review.get("archive_entry") or "").strip()
    if archive_entry:
        upsert_formal_line(ROOT, ARCHIVE.name, REVIEW_ARCHIVE_START, REVIEW_ARCHIVE_END, market_date, archive_entry, before_heading="## 6. 历史Excel与专项数据来源")
    _purge_case_mapping_rows()
    experience_entry = str(review.get("experience_entry") or "").strip()
    if experience_entry:
        case_mode = str(review.get("case_mode") or "").upper()
        case_id = str(review.get("case_id") or "").strip()
        if case_mode.startswith("NEW_CASE_FROM_EXECUTED_") and case_id:
            case_entry = _case_detail_projection_entry(experience_entry, case_id)
            upsert_formal_line(ROOT, EXPERIENCE.name, CASE_DETAILS_START, CASE_DETAILS_END, case_id, case_entry, before_heading="## 3. 历史研究与专项回测")
        else:
            upsert_formal_line(ROOT, EXPERIENCE.name, REVIEW_EXPERIENCE_START, REVIEW_EXPERIENCE_END, market_date, experience_entry, before_heading="## 5. 研究与经验转化")
    _persist_existing_case_detail_projections(review)
    sync_experience_case_mapping_index(review)
    record_close_review_closure(account, request, review, event)


def record_post_close_review(account: dict, request: dict) -> tuple[bool, bool]:
    review = request.get("formal_review")
    if request.get("interaction_scenario") != "POST_CLOSE_REVIEW" or not review:
        return False, False
    review_scope = str(review.get("review_scope") or "").strip().upper()
    request_phase = str(request.get("market_phase") or request.get("session_stage") or "").strip().upper()
    review_phase = str(review.get("market_phase") or review.get("session_stage") or "").strip().upper()
    if review_scope == "MORNING_SESSION_STAGE_ONLY" or "MIDDAY_BREAK" in {request_phase, review_phase}:
        return False, False
    market_date = str(review.get("market_date") or request.get("market_date") or account.get("last_confirmed_market_date") or "")
    if not market_date:
        raise RuntimeError("POST_CLOSE_REVIEW requires market_date")
    event_path = ROOT / "events" / "reviews" / f"{market_date}.json"
    prior = load_json(event_path) if event_path.exists() else {}
    prior_review = prior.get("review") if isinstance(prior.get("review"), dict) else {}
    incoming_time = parse_time(
        review.get("reviewed_at_beijing")
        or review.get("updated_at_beijing")
        or request.get("requested_at_beijing")
        or request.get("request_time_beijing")
        or account.get("updated_at")
    )
    prior_time = parse_time(
        prior.get("reviewed_at_beijing")
        or prior.get("updated_at_beijing")
        or prior.get("account_updated_at")
    )
    replay_received = parse_time(request.get("requested_at_beijing") or request.get("request_time_beijing"))
    prior_updated = parse_time(prior.get("updated_at_beijing"))
    if prior_updated and replay_received and replay_received < prior_updated and review == prior_review:
        # Exact replay of the already-persisted canonical review: only repair
        # its CASE human projection. Do not revalidate historical PIT against
        # today's account, or rewrite the event/closure/current state.
        _persist_existing_case_detail_projections(prior_review)
        return True, True
    current_path = ROOT / "data" / "state" / "CURRENT.json"
    current = load_json(current_path) if current_path.exists() else {}
    close_contract = build_close_data_contract(ROOT, current)
    data_time = dict(review.get("data_time")) if isinstance(review.get("data_time"), dict) else {}
    supplied_close_snapshot = str(data_time.get("close_snapshot") or review.get("close_snapshot") or "").strip()
    canonical_close_snapshot = str(close_contract.get("latest_snapshot") or "").strip()
    canonical_close_verified = (
        close_contract.get("status") == "VERIFIED_SESSION_CLOSE"
        and close_contract.get("verified_session_close") is True
        and close_contract.get("market_date") == market_date
        and bool(canonical_close_snapshot)
    )
    # A present verified close with a mismatched or absent request reference is
    # a stale/ambiguous input, not a missing-close case. Do not silently turn a
    # review computed against another snapshot into a degraded formal review.
    if canonical_close_verified and supplied_close_snapshot != canonical_close_snapshot:
        return False, False
    close_verified = canonical_close_verified and supplied_close_snapshot == canonical_close_snapshot
    # Review completion is independent from close-data completeness. Preserve
    # the canonical close contract when available; otherwise persist an explicit
    # degraded review with the missing/invalid evidence boundary. Never invent
    # a close snapshot or promote an intraday fact to 15:00.
    if close_verified:
        data_time["close_data_status"] = "VERIFIED_SESSION_CLOSE"
        data_time["close_snapshot"] = canonical_close_snapshot
        data_time.pop("close_data_gap", None)
    else:
        data_time["close_data_status"] = str(close_contract.get("status") or "UNVERIFIED")
        data_time["close_data_gap"] = (
            "VERIFIED_SESSION_CLOSE unavailable for this market date; review completed "
            "with degraded/incomplete evidence and no inferred close."
        )
        data_time.pop("close_snapshot", None)
    review = dict(review)
    review["data_time"] = data_time
    lifecycle_error = validate_current_lifecycle_contract(review.get("lifecycle"), account)
    if lifecycle_error:
        raise ValueError(f"invalid formal review lifecycle contract: {lifecycle_error}")
    review_lifecycle = review.get("holding_actions") or review.get("lifecycle")
    managed_error = validate_managed_position_lifecycle(review_lifecycle, account, "formal_review.managed_positions")
    if managed_error:
        raise ValueError(f"invalid formal review managed-position contract: {managed_error}")
    managed_projection = build_managed_position_projection(ROOT, account)
    review = _normalize_executed_trade_case_mapping(review, market_date)
    payload = {"market_date": market_date, "account_updated_at": account.get("updated_at"), "formal_review": review}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    prior = load_json(event_path) if event_path.exists() else {}
    if prior.get("fingerprint") == fingerprint:
        # Event existence is not proof that downstream projections completed.
        persisted_review = prior.get("review") if isinstance(prior.get("review"), dict) else review
        _persist_post_close_review_projections(account, request, persisted_review, prior)
        return True, True
    if prior_time and incoming_time and incoming_time < prior_time:
        # A stale review cannot replace canonical event/PIT facts, but its
        # already-canonical lifecycle mapping may still repair human CASE
        # projections through the same formal-file gateway.
        prior_review = prior.get("review") if isinstance(prior.get("review"), dict) else {}
        _persist_existing_case_detail_projections(prior_review)
        return True, True
    review_time = (incoming_time or datetime.now(SHANGHAI)).isoformat(timespec="seconds")
    event = {"event_type": "FORMAL_POST_CLOSE_REVIEW", "market_date": market_date, "account_updated_at": account.get("updated_at"), "fingerprint": fingerprint, "request_id": request.get("request_id"), "review": review, "managed_position_sell_review": managed_projection, "updated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
    event["reviewed_at_beijing"] = review_time
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(event_path, event)
    _persist_post_close_review_projections(account, request, review, event)
    return True, False


def record_close_review_closure(account: dict, request: dict, review: dict, event: dict) -> None:
    """Persist the completed review chain without creating a second review store."""
    market_date = str(event.get("market_date") or "")
    if not market_date:
        return
    closure_path = ROOT / "data" / "state" / f"close_review_closure_{market_date}.json"
    snapshot = review.get("data_time") or {}
    closure = {
        "schema_version": "1.0", "market_date": market_date,
        "chain": ["market_close", "account", "formal_review", "archive", "experience", "dashboard", "risk"],
        "account_fact_updated_at": account.get("updated_at", ""),
        "close_snapshot": snapshot.get("close_snapshot") or review.get("close_snapshot", ""),
        "formal_review_path": f"events/reviews/{market_date}.json",
        "dashboard_path": "ETF当前状态_DASHBOARD.md", "archive_path": "ETF市场行情档案_2026.md",
        "experience_path": "ETF交易复盘与经验库_2026.md",
        "case_id": review.get("case_id") or "", "case_mode": review.get("case_mode") or "CONTINUATION_NO_NEW_CASE",
        "known_net_equity": (review.get("etf_strategy_known_net") or {}).get("known_net_strategy_equity"),
        "risk_rate_pct": (review.get("etf_strategy_known_net") or {}).get("etf_strategy_risk_rate_pct"),
        "status": "CLOSED", "reviewed_at_beijing": event.get("reviewed_at_beijing", ""),
        "request_id": request.get("request_id", ""),
        "note": "Formal post-close review is canonicalized from the request-scoped review payload; no trade or permission is inferred.",
    }
    prior = load_json(closure_path) if closure_path.exists() else {}
    if prior == closure:
        return
    atomic_json_write(closure_path, closure)
    current_path = ROOT / "data" / "state" / "CURRENT.json"
    if current_path.exists():
        current = load_json(current_path)
        current["close_review_closure"] = {
            "status": "CLOSED", "market_date": market_date,
            "path": str(closure_path.relative_to(ROOT)).replace("\\", "/"),
            "formal_review_path": f"events/reviews/{market_date}.json", "case_id": closure.get("case_id", ""),
        }
        atomic_json_write(current_path, current)


def execution_attribution(trade: dict, linked_decision_id: str) -> dict:
    if not linked_decision_id:
        return {"status": "NO_LINKED_DECISION"}
    path = ROOT / "events/decisions" / f"{linked_decision_id}.json"
    if not path.exists():
        return {"status": "LINKED_DECISION_NOT_FOUND"}
    decision = load_json(path)
    try:
        from decision_trade_link import decision_price_for_trade
    except ModuleNotFoundError:
        from scripts.decision_trade_link import decision_price_for_trade
    # Attribute price only to the actual traded object/action.  candidate_code
    # may describe a simultaneous observation or new opportunity.
    dprice = safe_float(decision_price_for_trade(decision, trade))
    eprice = safe_float(trade.get("price"))
    diff_pct = pct(eprice, dprice) if dprice not in (None, 0.0) and eprice is not None else None
    side = str(trade.get("side") or "").upper()
    is_buy = side in {"BUY", "B", "买", "买入"} or "买" in side
    is_sell = side in {"SELL", "S", "卖", "卖出"} or "卖" in side
    adverse = diff_pct if is_buy else (-diff_pct if is_sell and diff_pct is not None else None)
    dt0 = parse_time(decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing") or decision.get("decision_time_beijing"))
    dt1 = parse_time(trade.get("confirmed_at_beijing"))
    delay = round((dt1 - dt0).total_seconds(), 1) if dt0 and dt1 else None
    ordering = str(decision.get("decision_effective_ordering") or "").upper()
    timing_quality = str(decision.get("timing_quality") or "").upper()
    bounded_before_execution = ordering == "BEFORE_EXECUTION" and timing_quality in {"USER_CONFIRMED_BOUNDED", "USER_CONFIRMED"}
    candidate_code = str(decision.get("candidate_code") or "").strip()
    hypothesis_id = decision.get("hypothesis_id") if candidate_code and candidate_code == str(trade.get("code") or "").strip() else None
    return {"status": "READY" if dprice is not None and eprice is not None and (delay is None or delay >= 0 or bounded_before_execution) else "PARTIAL", "decision_id": linked_decision_id, "hypothesis_id": hypothesis_id, "decision_price": dprice, "execution_price": eprice, "execution_price_vs_decision_pct": diff_pct, "adverse_execution_cost_pct": adverse, "decision_to_execution_seconds": delay if delay is None or delay >= 0 else None, "decision_effective_ordering": decision.get("decision_effective_ordering"), "timing_quality": decision.get("timing_quality"), "timing_status": "EXACT" if delay is not None and delay >= 0 else "BOUNDED_BEFORE_EXECUTION" if bounded_before_execution else "UNAVAILABLE", "method_note": "正的adverse_execution_cost_pct表示相对正式决策价格出现不利执行偏差；买入价更高或卖出价更低均为正。该指标分离判断质量与执行质量，不改变交易权限。"}



def _trade_idempotency_key(trade: dict, confirmed_at: str) -> str:
    explicit = str(trade.get("idempotency_key") or "").strip()
    return explicit or "|".join(str(trade.get(key) or "") for key in ("code", "side", "quantity", "price")) + "|" + str(confirmed_at)


def _find_existing_trade(trade: dict, confirmed_at: str, key: str) -> dict | None:
    directory = ROOT / "events" / "trades"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try: prior = load_json(path)
        except Exception: continue
        if prior.get("idempotency_key") == key or (all(str(prior.get(k) or "") == str(trade.get(k) or "") for k in ("code", "side", "quantity", "price")) and str(prior.get("confirmed_at_beijing") or "") == str(confirmed_at)):
            return prior
    return None


def resolve_trade_linked_decision_id(
    trade: dict,
    decision_id: str = "",
    existing_linked_decision_id: str = "",
) -> str:
    """Resolve one durable decision association without weakening legacy inputs.

    An already-persisted association wins on replay.  For a new event, an
    explicit trade-event link is authoritative, followed by the legacy
    decision_id and same-request formal decision compatibility.
    """
    for value in (
        existing_linked_decision_id,
        trade.get("linked_decision_id"),
        trade.get("decision_id"),
        decision_id,
    ):
        resolved = str(value or "").strip()
        if resolved:
            return resolved
    return ""

def _latest_trade_event_id() -> str:
    directory = ROOT / "events" / "trades"
    candidates = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            event = load_json(path)
        except Exception:
            continue
        event_id = str(event.get("event_id") or "")
        if event_id:
            candidates.append((str(event.get("confirmed_at_beijing") or ""), event_id))
    return max(candidates)[1] if candidates else ""



def _account_change_events(prior: dict, current: dict, request: dict, trade: dict | None) -> list[dict]:
    """Record only observable account deltas; never infer an unexplained trade."""
    prior_positions = {str(x.get("code")): x for x in (prior.get("positions") or []) if x.get("code")}
    current_positions = {str(x.get("code")): x for x in (current.get("positions") or []) if x.get("code")}
    codes = sorted(set(prior_positions) | set(current_positions))
    confirmed_code = str((trade or {}).get("code") or "")
    confirmed_side = str((trade or {}).get("side") or "").upper()
    confirmed_qty = safe_float((trade or {}).get("quantity"))
    event_type = str(request.get("account_change_event_type") or "").strip() or (
        "USER_REPORTED_TRADE" if trade else "BROKER_SCREENSHOT_CHANGE"
    )
    event_time = str((trade or {}).get("confirmed_at_beijing") or current.get("updated_at") or "")
    events: list[dict] = []
    for code in codes:
        before = safe_float(prior_positions.get(code, {}).get("quantity")) or 0.0
        after = safe_float(current_positions.get(code, {}).get("quantity")) or 0.0
        delta = round(after - before, 8)
        if delta == 0:
            continue
        row = current_positions.get(code) or prior_positions.get(code) or {}
        explained = bool(
            trade and code == confirmed_code and confirmed_qty is not None
            and abs(abs(delta) - confirmed_qty) < 1e-8
            and ((delta > 0 and confirmed_side in {"BUY", "B", "买入", "买"})
                 or (delta < 0 and confirmed_side in {"SELL", "S", "卖出", "卖"}))
        )
        known_ipo = _is_known_ipo_registration(current, code, delta, after)
        key_body = {"event_type": event_type, "code": code, "delta": delta, "event_time": event_time}
        key = "account_change_" + hashlib.sha256(json.dumps(key_body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        events.append({
            "event_id": key, "idempotency_key": key, "event_type": event_type,
            "event_time": event_time, "object": display_name(row),
            "code": code, "quantity_before": before, "quantity_after": after,
            "quantity_delta": delta,
            "change_summary": ("买入/新增持仓" if delta > 0 else "卖出/减少持仓"),
            "reconciliation_status": (
                "RECONCILED_BY_CONFIRMED_TRADE" if explained
                else "RECONCILED_BY_KNOWN_IPO_REGISTRATION" if known_ipo
                else "UNRECONCILED_ACCOUNT_CHANGE"
            ),
            "source": str(current.get("source") or request.get("source") or "USER_CONFIRMED"),
            "read_only": True,
        })
    for field in ("cash", "total_asset"):
        before = safe_float(prior.get(field))
        after = safe_float(current.get(field))
        if before is None or after is None or abs(after - before) < 0.005:
            continue
        key_body = {"event_type": event_type, "field": field, "delta": round(after - before, 2), "event_time": event_time}
        key = "account_change_" + hashlib.sha256(json.dumps(key_body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        events.append({
            "event_id": key, "idempotency_key": key, "event_type": event_type,
            "event_time": event_time, "object": field, "change_summary": f"{field}变化 {after - before:+.2f}",
            "amount_before": before, "amount_after": after, "amount_delta": round(after - before, 2),
            "reconciliation_status": "RECONCILED_BY_CONFIRMED_TRADE" if trade else "UNRECONCILED_ACCOUNT_CHANGE",
            "source": str(current.get("source") or request.get("source") or "USER_CONFIRMED"),
            "read_only": True,
        })
    return events


def _is_known_ipo_registration(account: dict, code: str, delta: float, after: float) -> bool:
    """Attribute a new position to one exact settled IPO obligation."""
    if delta <= 0 or after <= 0:
        return False
    positions = [p for p in account.get("positions") or [] if str(p.get("code") or "") == code]
    obligations = [
        o for o in account.get("settlement_obligations") or []
        if str(o.get("status") or "").upper() == "SETTLED"
        and str(o.get("obligation_type") or "").upper() == "IPO_ALLOTMENT_PAYMENT"
        and str(o.get("security_code") or "") == code
    ]
    if len(positions) != 1 or len(obligations) != 1:
        return False
    position, obligation = positions[0], obligations[0]
    quantity = safe_float(position.get("quantity"))
    price = safe_float(obligation.get("subscription_price"))
    cost = safe_float(position.get("cost"))
    required_cash = safe_float(obligation.get("required_cash"))
    if None in (quantity, price, cost, required_cash):
        return False
    return (
        abs(quantity - after) <= 1e-8
        and price > 0
        and abs(cost - price) <= max(0.01, price * 0.0001)
        and abs(required_cash - after * price) <= 0.01
    )


def _reconcile_known_ipo_events(account: dict) -> None:
    """Upgrade matching historical deltas without re-emitting notifications."""
    for event in account.get("account_change_events_after_confirmed_at") or []:
        if str(event.get("reconciliation_status") or "").upper() != "UNRECONCILED_ACCOUNT_CHANGE":
            continue
        code = str(event.get("code") or "").strip()
        delta = safe_float(event.get("quantity_delta"))
        after = safe_float(event.get("quantity_after"))
        if code and delta is not None and after is not None and _is_known_ipo_registration(account, code, delta, after):
            event["reconciliation_status"] = "RECONCILED_BY_KNOWN_IPO_REGISTRATION"


def _apply_trade_to_account(prior: dict, trade: dict) -> dict:
    """Safely carry a user-confirmed trade into the account fact when no screenshot is supplied."""
    account = json.loads(json.dumps(prior))
    account.setdefault("positions", [])
    code = str(trade.get("code") or "")
    side = str(trade.get("side") or "").upper()
    qty = safe_float(trade.get("quantity"))
    amount = safe_float(trade.get("amount"))
    if amount is None:
        price = safe_float(trade.get("price"))
        amount = round(price * qty, 6) if price is not None else None
    if not code or qty is None or qty <= 0 or amount is None:
        return account
    sign = 1 if side in {"BUY", "B", "买入", "买"} else -1 if side in {"SELL", "S", "卖出", "卖"} else 0
    if sign == 0:
        return account
    position = next((p for p in account["positions"] if str(p.get("code")) == code), None)
    if position is None:
        if sign < 0:
            return account
        position = {"asset_type": trade.get("asset_type") or "ETF", "name": trade.get("name") or code, "code": code, "quantity": 0, "cost": safe_float(trade.get("price")) or 0, "last_price": safe_float(trade.get("price")) or 0, "market_value": 0, "holding_pnl": 0, "holding_pnl_pct": 0}
        account["positions"].append(position)
    before = safe_float(position.get("quantity")) or 0.0
    after = before + sign * qty
    if after < -1e-8:
        return account
    position["quantity"] = int(after) if abs(after - round(after)) < 1e-8 else after
    if sign < 0 and after <= 1e-8:
        # A fully executed exit must not leave an impossible residual
        # available quantity. Partial sells retain existing semantics.
        position["quantity"] = 0
        position["available_quantity"] = 0
    elif sign < 0 and position.get("available_quantity") is not None:
        available = safe_float(position.get("available_quantity"))
        if available is not None:
            position["available_quantity"] = max(0, min(after, available - qty))
    if sign > 0 and position.get("cost") in (None, 0, 0.0):
        position["cost"] = safe_float(trade.get("price")) or position.get("cost") or 0
    if position.get("last_price") in (None, 0, 0.0):
        position["last_price"] = safe_float(trade.get("price")) or 0
    position["market_value"] = round(float(position.get("last_price") or 0) * float(position["quantity"]), 2)
    cash_delta = -sign * amount
    account["cash"] = round((safe_float(account.get("cash")) or 0) + cash_delta, 2)
    if account.get("total_asset") is not None:
        account["total_asset"] = round((safe_float(account.get("total_asset")) or 0) + cash_delta, 2)
    account["updated_at"] = str(trade.get("confirmed_at_beijing") or account.get("updated_at") or datetime.now(SHANGHAI).isoformat(timespec="seconds"))
    account["last_confirmed_market_date"] = account["updated_at"][:10]
    account["status"] = "VALID"
    account["source"] = str(trade.get("source") or "USER_REPORTED_TRADE_CONFIRMED")
    account["validity_mode"] = "EVENT_DRIVEN_TRADE_CONFIRMED"
    return account




def _explicit_case_ids_from_text(value: object) -> list[str]:
    return sorted(set(re.findall(r"CASE-\d{8}-\d{2}", str(value or ""))))


def _review_case_ids_for_trade(event: dict) -> list[str]:
    """Read only existing formal review mappings; never create a CASE mapping."""
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        return []
    found = set()
    review_dir = ROOT / "events" / "reviews"
    for path in sorted(review_dir.glob("*.json")) if review_dir.exists() else []:
        try:
            payload = load_json(path)
        except (OSError, ValueError, TypeError):
            continue

        def walk(value):
            if isinstance(value, dict):
                if str(value.get("trade_event_id") or "").strip() == event_id:
                    case_id = str(value.get("case_id") or "").strip()
                    if case_id:
                        found.add(case_id)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(payload)
    return sorted(found)


def _case_ids_from_existing_experience_for_trade(event: dict, text: str, table_start: int, table_end: int) -> list[str]:
    """Recover an already documented CASE owner using exact immutable trade facts."""
    event_id = str(event.get("event_id") or "").strip()
    code = str(event.get("code") or "").strip()
    name = str(event.get("name") or "").strip()
    side = str(event.get("side") or "").upper()
    side_cn = "买入" if side in {"BUY", "B", "买入", "买"} else "卖出" if side in {"SELL", "S", "卖出", "卖"} else side
    qty = int(float(event.get("quantity") or 0))
    price = float(event.get("price") or 0)
    confirmed_at = str(event.get("confirmed_at_beijing") or "")
    trade_date = confirmed_at[:10]
    date_time_tokens = set()
    if len(confirmed_at) >= 19 and confirmed_at[5:7].isdigit() and confirmed_at[8:10].isdigit():
        month = str(int(confirmed_at[5:7]))
        day = str(int(confirmed_at[8:10]))
        clock = confirmed_at[11:19]
        date_time_tokens.update({f"{month}月{day}日{clock}", f"{confirmed_at[5:7]}月{confirmed_at[8:10]}日{clock}", f"{trade_date} {clock}"})
    elif trade_date:
        date_time_tokens.add(trade_date)
    marker = f"TRADE_EVENT:{event_id}" if event_id else ""
    old_case_ids = []
    table_text = text[table_start:table_end]
    for line in table_text.splitlines():
        if (marker and marker in line) or (
            trade_date and f"|{trade_date} " in line and f"|{code}|" in line
            and f"|{side_cn}|" in line and f"|{qty:,}|" in line and f"|{price:.3f}|" in line
        ):
            old_case_ids.extend(_explicit_case_ids_from_text(line))

    # The CASE section is a human-maintained formal owner record.  Consume it
    # only when it contains either the exact event marker/linked decision, or
    # all immutable trade facts; code/date alone is intentionally insufficient.
    linked = str(event.get("linked_decision_id") or "").strip()
    heading = re.compile(r"^###\s+.*?(CASE-\d{8}-\d{2}).*?$", re.MULTILINE)
    matches = list(heading.finditer(text))
    found = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.start():end]
        exact_identity = (
            (code and code in section or name and name in section)
            and side_cn and side_cn in section
            and (f"{qty:,}" in section or str(qty) in section)
            and f"{price:.3f}" in section
            and any(token in section for token in date_time_tokens)
        )
        if (marker and marker in section) or (linked and linked in section) or exact_identity:
            found.add(match.group(1))
    return sorted(found)


def _case_ids_for_transaction_projection(event: dict, text: str, table_start: int, table_end: int) -> list[str]:
    """Resolve CASE ownership with strong facts first and no guessing."""
    event_id = str(event.get("event_id") or "").strip()
    event_date = str(event.get("execution_date") or event.get("confirmed_at_beijing") or "")[:10]
    if event_id and event_date:
        terminal_path = ROOT / "events" / "reviews" / f"{event_date}.json"
        if terminal_path.exists():
            try:
                from review_prerequisite_lifecycle import is_valid_unrecoverable_review_event
                terminal_review = json.loads(terminal_path.read_text(encoding="utf-8"))
                if is_valid_unrecoverable_review_event(terminal_review, event_id):
                    # An unrecoverable review is a canonical terminal lifecycle
                    # fact, not a normal CASE owner.  Do not project a stale
                    # CASE from an older transaction-index row.
                    return []
            except (OSError, ValueError, TypeError, ImportError):
                pass
    review_ids = _review_case_ids_for_trade(event)
    if review_ids:
        return review_ids
    return _case_ids_from_existing_experience_for_trade(event, text, table_start, table_end)


def sync_experience_transaction_index(event: dict) -> None:
    """Upsert a confirmed trade into Experience §2.1 and keep its counts aligned.

    This is fact synchronization only. It never evaluates trade quality, changes
    lifecycle rules, or creates a trading action.
    """
    import re

    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    text = EXPERIENCE.read_text(encoding="utf-8")
    section_end = "\n### 2.2 银证转账与非交易现金流水"
    if section_end not in text:
        raise RuntimeError("experience transaction-index boundary missing")

    marker = f"TRADE_EVENT:{event_id}"
    confirmed = str(event.get("confirmed_at_beijing") or "")
    dt = confirmed.replace("T", " ")[:19]
    name = str(event.get("name") or event.get("code") or "")
    code = str(event.get("code") or "")
    side = str(event.get("side") or "").upper()
    side_cn = "买入" if side in {"BUY", "B", "买入", "买"} else "卖出" if side in {"SELL", "S", "卖出", "卖"} else side
    qty = int(float(event.get("quantity") or 0))
    price = float(event.get("price") or 0)
    gross = float(event.get("amount") or price * qty)
    # Older confirmed trade events use the canonical ``fee`` field while
    # correction-enriched events use ``fee_amount``.  Treat both as the same
    # trade-fact fee; a confirmed status still remains mandatory.
    fee = safe_float(event.get("fee_amount"))
    if fee is None:
        fee = safe_float(event.get("fee"))
    fee_status = str(event.get("fee_status") or "").upper()
    fee_confirmed = fee is not None and fee_status in {"CONFIRMED", "KNOWN", "FINAL"}
    fee_text = f"{fee:.2f}" if fee_confirmed else "待确认"
    if side_cn == "买入":
        cash = -(gross + (fee if fee_confirmed and fee is not None else 0))
    elif side_cn == "卖出":
        cash = gross - (fee if fee_confirmed and fee is not None else 0)
    else:
        cash = 0.0
    cash_text = f"{cash:,.2f}" if fee_confirmed else f"{cash:,.2f}（未含待确认费用）"
    lifecycle = str(event.get("lifecycle") or "").strip()
    if lifecycle in {"待确认", "UNKNOWN", "未提供"}:
        lifecycle = ""
    linked = str(event.get("linked_decision_id") or "").strip()
    # Preserve the strongest already-existing CASE ownership while rewriting
    # the managed transaction row.  This is projection-only: no CASE is created.
    table_header = "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|"
    projection_table_start = text.index(table_header)
    projection_table_end = text.index(section_end, projection_table_start)
    case_ids = _case_ids_for_transaction_projection(
        event, text, projection_table_start, projection_table_end
    )
    note_parts = list(case_ids)
    if lifecycle:
        note_parts.append(lifecycle)
    if linked:
        note_parts.append(f"关联决策{linked}")
    note_parts.append("真实成交已执行")
    note = "；".join(note_parts)
    row = f"|{dt}|{name}（{code}）|{code}|{side_cn}|{qty:,}|{price:.3f}|{gross:,.2f}|{fee_text}|{cash_text}|{note}| <!-- {marker} -->"

    table_start = text.index("|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|")
    table_end = text.index(section_end, table_start)
    table = text[table_start:table_end]
    lines = table.splitlines()
    replacement_index = None
    for index, line in enumerate(lines):
        if marker in line:
            replacement_index = index
            break
    if replacement_index is None:
        # Older managed rows may predate TRADE_EVENT markers.  Match the
        # immutable trade identity before appending, so a fee correction repairs
        # that row instead of creating a duplicate transaction.
        identity = f"|{dt}|{name}（{code}）|{code}|{side_cn}|{qty:,}|{price:.3f}|{gross:,.2f}|"
        for index, line in enumerate(lines):
            if line.startswith(identity):
                replacement_index = index
                break
    if replacement_index is None:
        idx = text.index(section_end)
        text = text[:idx].rstrip() + "\n" + row + "\n" + text[idx:]
    else:
        lines[replacement_index] = row
        updated_table = "\n".join(lines)
        text = text[:table_start] + updated_table + text[table_end:]

    # Recompute the unique index counts from the actual table instead of carrying
    # a hand-maintained number that can lag a newly confirmed fill.
    table_start = text.index("|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|")
    table_end = text.index(section_end, table_start)
    rows = []
    for line in text[table_start:table_end].splitlines():
        if re.match(r"^\|20\d{2}-\d{2}-\d{2} ", line):
            rows.append(line)
    total = len(rows)
    etf_count = sum("ETF（" in line for line in rows)
    stock_count = total - etf_count
    dates = [line.split("|")[1][:10] for line in rows]
    last_date = max(dates) if dates else ""
    text = re.sub(
        r"统计区间为2026-07-13至\d{4}-\d{2}-\d{2}，共\d+笔证券交易：ETF \d+笔、个股\d+笔。",
        f"统计区间为2026-07-13至{last_date}，共{total}笔证券交易：ETF {etf_count}笔、个股{stock_count}笔。",
        text,
        count=1,
    )
    text = re.sub(r"不属于\d+笔证券交易", f"不属于{total}笔证券交易", text, count=1)
    table_start = text.index("|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|")
    table_end = text.index(section_end, table_start)
    text = _refresh_current_fee_projection(text, table_start, table_end)
    write_formal_text_if_changed(ROOT, EXPERIENCE.name, text)


def _refresh_current_fee_projection(text: str, table_start: int, table_end: int) -> str:
    """Project current cumulative ETF fees from canonical facts without rewriting PIT history."""
    try:
        from confirmed_trade_facts import canonical_etf_fee_projection
        equity_path = ROOT / "data" / "state" / "etf_strategy_equity.json"
        equity = load_json(equity_path) if equity_path.exists() else {}
        projection = canonical_etf_fee_projection(ROOT, equity.get("trades") or [])
    except (OSError, ValueError, TypeError, ImportError):
        return text
    rows = [line for line in text[table_start:table_end].splitlines() if re.match(r"^\|20\d{2}-\d{2}-\d{2} ", line)]
    dates = [line.split("|")[1][:10] for line in rows]
    last_date = max(dates) if dates else ""
    pending = "无" if projection["pending_fee_count"] == 0 else f"{projection['pending_fee_count']}笔"
    sentence = f"截至{last_date}累计已确认ETF费用{projection['effective_confirmed_fee_sum']:.2f}元；待确认费用：{pending}。"
    return re.sub(r"截至.*?。", sentence, text, count=1)

def write_trade_review_required(event: dict) -> None:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    path = ROOT / "events" / "research" / "decision_outcomes" / f"TRADE_COMPLETED_REVIEW_REQUIRED_{event_id}.json"
    if path.exists():
        return
    atomic_json_write(path, {
        "event_type": "TRADE_COMPLETED_REVIEW_REQUIRED",
        "event_id": event_id,
        "notification_id": event.get("notification_id"),
        "related_decision_id": event.get("linked_decision_id"),
        "execution_date": event.get("execution_date"),
        "confirmation_date": event.get("confirmation_date"),
        "security_code": event.get("code"),
        "security_name": event.get("name"),
        "side": event.get("side"),
        "quantity": event.get("quantity"),
        "price": event.get("price"),
        "amount": event.get("amount"),
        "review_status": "PENDING_FORMAL_REVIEW",
        "objective_fields": ["trade_event", "strategy_risk_rate_change", "cash_change", "next_observation_point"],
        "safety_boundary": "只生成客观复盘待办，不自动判断交易正确/错误，不修改MASTER，不生成新的交易动作。",
    })


def _explicit_fact_type(request: dict) -> str:
    return str(
        request.get("formal_fact_type")
        or request.get("fact_type")
        or request.get("ingress_intent")
        or ""
    ).strip().upper()


def canonical_ingress_contract_for_request(request: dict) -> dict:
    """Return the actor-side terminal canonical ingress contract for formal facts."""

    fact_type = _explicit_fact_type(request)
    scenario = str(request.get("interaction_scenario") or "").strip().upper()
    channel = str(request.get("channel") or request.get("delivery_channel") or "").strip().upper()
    report_type = str(request.get("report_type") or request.get("report_kind") or "").strip().upper()

    is_broker_fact = is_broker_screenshot_request(request)
    is_review_fact = fact_type in {"FORMAL_POST_CLOSE_REVIEW", "POST_CLOSE_REVIEW"} or scenario == "POST_CLOSE_REVIEW"
    is_trade_fact = fact_type in {"CONFIRMED_TRADE", "TRADE_EVENT"} or isinstance(request.get("trade_event"), dict)
    is_decision_fact = fact_type == "FORMAL_DECISION" or isinstance(request.get("formal_decision"), dict)
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

    if is_broker_fact:
        if isinstance(request.get("account_fact"), dict) or isinstance(request.get("trade_event"), dict):
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
        if isinstance(request.get("trade_event"), dict):
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


def account_fact_is_older(prior: dict, supplied: dict) -> bool:
    old_time = parse_time(prior.get("updated_at"))
    new_time = parse_time(supplied.get("updated_at"))
    return bool(old_time and new_time and new_time < old_time)



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    req_path = (ROOT / args.request_path).resolve()
    if ROOT not in req_path.parents or not req_path.exists():
        raise RuntimeError("invalid state sync request path")
    request = load_json(req_path)
    request["_ingress_path"] = str(req_path.relative_to(ROOT)).replace("\\\\", "/")
    canonical_ingress_contract = canonical_ingress_contract_for_request(request)
    trade = request.get("trade_event")
    prior_account = load_json(ACCOUNT) if ACCOUNT.exists() else {}
    supplied_account = request.get("account_fact")
    confirmed_at = (
        trade.get("confirmed_at_beijing") or request.get("requested_at_beijing")
        or trade.get("executed_at") or prior_account.get("updated_at")
    ) if isinstance(trade, dict) else ""
    replay_key = _trade_idempotency_key(trade, confirmed_at) if isinstance(trade, dict) else ""
    existing_trade = _find_existing_trade(trade, confirmed_at, replay_key) if isinstance(trade, dict) else None
    account_sync_status = "NOT_APPLICABLE"
    if not supplied_account and isinstance(trade, dict) and existing_trade is None:
        supplied_account = _apply_trade_to_account(prior_account, trade)
    if is_broker_screenshot_request(request) and not isinstance(supplied_account, dict) and not isinstance(trade, dict):
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
        return 0
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
            "request": str(req_path.relative_to(ROOT)).replace("\\\\", "/"),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

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
            new_events = _account_change_events(prior_account, supplied_account, request, trade)
            known = {str(x.get("idempotency_key") or x.get("event_id") or "") for x in prior_events}
            supplied_account["account_change_events_after_confirmed_at"] = prior_events + [x for x in new_events if str(x.get("idempotency_key")) not in known]
            if supplied_account == prior_account:
                account_sync_status = "ACCOUNT_SYNC_IDEMPOTENT_NOOP"
            else:
                account_sync_status = "ACCOUNT_FACT_UPDATED"
                atomic_json_write(ACCOUNT, supplied_account)
    account = load_json(ACCOUNT)
    latest_trade_event_id = _latest_trade_event_id()
    if latest_trade_event_id:
        current_path = ROOT / "data/state/CURRENT.json"
        current = load_json(current_path) if current_path.exists() else {}
        if current.get("last_trade_event_id") != latest_trade_event_id:
            current["last_trade_event_id"] = latest_trade_event_id
            current["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            atomic_json_write(current_path, current)
    if account.get("status") != "VALID":
        raise RuntimeError("account_fact is not VALID")
    decision_recorded, decision_id = record_formal_decision(request)
    if decision_recorded:
        decision = request.get("formal_decision") or {}
        prior_action = account.get("formal_action") or {}
        same_executed_decision = (
            str(prior_action.get("decision_id") or "") == decision_id
            and str(prior_action.get("execution_status") or "").upper() == "EXECUTED"
        )
        # Replaying the same formal decision is idempotent: an executed action
        # must never be downgraded back to PENDING. A genuinely new decision
        # may still become the current pending action.
        if not same_executed_decision:
            account["formal_action"] = {"action": decision.get("action") or decision.get("amount_action") or "", "quantity": decision.get("quantity"), "decision_id": decision_id, "decision_time": decision.get("decision_time") or decision.get("data_as_of_beijing") or datetime.now(SHANGHAI).isoformat(timespec="seconds"), "source": "CHATGPT_FORMAL_DECISION", "lifecycle": decision.get("lifecycle"), "applicable_object": decision.get("candidate_code") or decision.get("code") or "", "validity": "ACTIVE", "execution_status": "PENDING"}
            atomic_json_write(ACCOUNT, account)
    trade_event_recorded = False
    if trade:
        confirmed_at = trade.get("confirmed_at_beijing") or request.get("requested_at_beijing") or trade.get("executed_at") or account.get("updated_at")
        idempotency_key = _trade_idempotency_key(trade, confirmed_at)
        existing = existing_trade or _find_existing_trade(trade, confirmed_at, idempotency_key)
        if existing:
            event, event_id, trade_event_recorded = existing, str(existing.get("event_id") or ""), True
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
            event_id = str(trade.get("event_id") or request.get("request_id") or datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S"))
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
        account["formal_action"] = {**(account.get("formal_action") or {}), "execution_status": "EXECUTED", "execution_fact_ref": f"events/trades/{event_id}.json", "last_executed_event_id": event_id}
        atomic_json_write(ACCOUNT, account)
        write_trade_review_required(event)
    sync_current_account_mirror(ROOT, account)
    review_recorded, review_idempotent = record_post_close_review(account, request)
    unavailable_recorded, unavailable_idempotent = (False, False)
    if trade:
        unavailable_recorded, unavailable_idempotent = record_unrecoverable_review_prerequisite(account, request, event)
    # Render after event/review persistence so a newly confirmed execution is
    # visible in the same canonical dashboard update, rather than one request
    # behind the machine facts.
    dashboard = replace_block(DASHBOARD.read_text(encoding="utf-8"), START, END, build_dashboard_block(account, latest_canonical_formal_decision(ROOT) or request.get("formal_decision"), request), insert_after_heading=True)
    write_formal_text_if_changed(ROOT, DASHBOARD.name, dashboard)
    # Keep the three human-readable fact documents synchronized even when the
    # request only confirms a fee/account snapshot and creates no new trade event.
    formal_files_sync = sync_formal_files(ROOT, account)
    result = {"ok": True, "request_id": request.get("request_id"), "interaction_scenario": request.get("interaction_scenario"), "account_updated_at": account.get("updated_at"), "dashboard_updated": True, "formal_decision_recorded": decision_recorded, "formal_decision_id": decision_id, "trade_event_recorded": trade_event_recorded, "post_close_review_recorded": review_recorded, "post_close_review_idempotent_noop": review_idempotent, "review_prerequisite_unavailable_recorded": unavailable_recorded, "review_prerequisite_unavailable_idempotent_noop": unavailable_idempotent, "formal_files_sync": formal_files_sync, "account_sync_status": account_sync_status, "canonical_ingress_state": canonical_ingress_contract["terminal_state"], "canonical_ingress_failure_reason": None if canonical_ingress_contract["terminal_state"] == CANONICAL_INGRESS_SUBMITTED else canonical_ingress_contract["reason"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

