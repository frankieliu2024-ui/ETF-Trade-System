from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median

from state_manager import atomic_json_write
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
    etfs = [p for p in positions if p.get("asset_type") == "ETF"]
    stocks = [p for p in positions if p.get("asset_type") == "STOCK"]
    etf_pnl = sum(float(p.get("holding_pnl") or 0) for p in etfs)
    # The formal risk rate is the maintained Known-net strategy return. Broker
    # floating PnL remains a separate holding-pressure fact.
    formal_risk = latest_formal_risk_fact()
    risk_rate = safe_float(formal_risk.get("risk_rate_pct"))
    formal_risk_equity = safe_float(formal_risk.get("strategy_equity_known_net"))
    if risk_rate is None:
        equity_path = ROOT / "data/state/etf_strategy_equity.json"
        equity = load_json(equity_path) if equity_path.exists() else {}
        equity_summary = equity.get("summary") or {}
        risk_rate = safe_float(equity_summary.get("known_net_current_strategy_return_pct"))
    if risk_rate is None:
        risk_rate = etf_pnl / 200000.0 * 100.0
    total_asset = float(account.get("total_asset") or 0)
    exposure = (float(account.get("stock_market_value") or 0) / total_asset * 100.0) if total_asset else 0.0
    scenario = request.get("interaction_scenario") or "UNSPECIFIED"
    lines = ["## 云端实时状态（自动同步）", "", f"> 更新时间：{account.get('updated_at','')}  ", f"> 来源：{account.get('source','')}  ", f"> 场景：{scenario}  ", "> 本区块只同步已确认账户事实与ChatGPT已形成的正式决策；自动程序不得自行推导交易权限或下单。", "", "|项目|最新事实|", "|-|-|", f"|总资产|{money(account.get('total_asset'))}|", f"|股票市值|{money(account.get('stock_market_value'))}|", f"|可用资金|{money(account.get('cash'))}|", f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|", f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|", f"|账户总风险暴露率|约{exposure:.2f}%|", f"|ETF持仓浮动盈亏|{money(etf_pnl)}|", *(([f"|ETF策略Known-net权益|{money(formal_risk_equity)}|"] if formal_risk_equity is not None else [])), f"|ETF策略风险率|约{risk_rate:.2f}%（Known-net；最新正式复盘风险事实优先，旧重建仅在无正式事实时回退）|", "", "### 当前持仓事实", "", "|标的|数量|成本|现价|市值|浮动盈亏|", "|-|-:|-:|-:|-:|-:|"]
    for p in positions:
        lines.append(f"|{display_name(p)}|{int(p.get('quantity') or 0):,}|{float(p.get('cost') or 0):.3f}|{float(p.get('last_price') or 0):.3f}|{money(p.get('market_value'))}|{money(p.get('holding_pnl'))}（{float(p.get('holding_pnl_pct') or 0):+.2f}%）|")
    lines += ["", f"持仓ETF：{'、'.join(display_name(p) for p in etfs) or '无'}。", f"账户个股：{'、'.join(display_name(p) for p in stocks) or '无'}。"]
    if decision:
        title = "最近一次正式收盘复盘" if scenario == "POST_CLOSE_REVIEW" else "最近一次正式盘中决策"
        lines += ["", f"### {title}", "", f"- 风险许可：{decision.get('risk_permission','未提供')}", f"- 生命周期：{decision.get('lifecycle','未提供')}", f"- 唯一主候选：{decision.get('main_candidate','无新的主候选。')}", f"- 金额与动作：{decision.get('amount_action','未提供')}", f"- 最大风险或0元主因：{decision.get('decisive_reason','未提供')}", f"- 决策数据时点：{decision.get('data_as_of_beijing','未提供')}"]
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


def select_point_in_time_snapshot(market_date: str, decision_time: str) -> tuple[str, dict, str]:
    cutoff = parse_time(decision_time)
    if not market_date or cutoff is None:
        return "", {}, "NO_VALID_DECISION_TIME"
    candidates = []
    for path in sorted((ROOT / "data/market/snapshots").glob(f"{market_date}_*.json")):
        try:
            snap = load_json(path)
        except Exception:
            continue
        if snap.get("market_date") != market_date or snap.get("quality_status") != "PASS":
            continue
        captured = parse_time(snap.get("captured_at_beijing") or snap.get("captured_at"))
        if captured is not None and captured <= cutoff:
            candidates.append((captured, path, snap))
    if not candidates:
        return "", {}, "NO_PRIOR_SNAPSHOT"
    _, path, snap = max(candidates, key=lambda x: x[0])
    return str(path.relative_to(ROOT)).replace("\\", "/"), snap, "POINT_IN_TIME_PRIOR_OR_EQUAL"


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
    return {"as_of_beijing": snapshot.get("captured_at_beijing"), "market_phase": snapshot.get("market_phase"), "etf_count": len(items), "items": items, "interpretation_rule": "保留正式决策时点的全ETF横截面证据，用于以后验证候选选择质量；当日涨跌排名只是描述维度，不是资本效率评分，不生成轮动动作。"}


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

    decision_time = str(decision.get("data_as_of_beijing") or "")
    if not decision_time:
        decision_time = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    cutoff = parse_time(decision_time)
    snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(market_date, decision_time)
    supplied_price = safe_float(decision.get("price_at_decision"))
    supplied_as_of = str(decision.get("price_as_of_beijing") or "")
    supplied_time = parse_time(supplied_as_of)
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
    if supplied_price is not None and not supplied_point_in_time and not price_source:
        pit_status = "SUPPLIED_PRICE_REJECTED_NO_VERIFIABLE_POINT_IN_TIME"

    hypothesis_id, hypothesis_link_status = resolve_hypothesis_id(decision, code, market_date, decision_id)
    lifecycle = str(decision.get("lifecycle") or "")
    hypothesis_closed = "退出" in lifecycle or str(decision.get("hypothesis_status") or "").upper() == "CLOSED"
    comparison = build_comparison_snapshot(snapshot) if snapshot else {"items": [], "interpretation_rule": "决策时点无可用历史快照，不使用未来数据补齐。"}
    event = {"event_type": "FORMAL_DECISION", "decision_id": decision_id, "fingerprint": fingerprint, "market_date": market_date, "decision_time_beijing": decision_time, "decision_effective_at_beijing": str(decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing") or ""), "decision_effective_ordering": str(decision.get("decision_effective_ordering") or ""), "timing_quality": str(decision.get("timing_quality") or ""), "timing_provenance": str(decision.get("timing_provenance") or ""), "interaction_scenario": request.get("interaction_scenario"), "candidate_code": code, "candidate_name": name, "hypothesis_id": hypothesis_id, "hypothesis_link_status": hypothesis_link_status, "hypothesis_closed": hypothesis_closed, "price_at_decision": price_at_decision, "price_as_of_beijing": price_as_of, "price_source_snapshot": snapshot_rel, "price_source": price_source, "point_in_time_status": pit_status, "comparison_snapshot": comparison, "formal_decision": decision, "read_only_research_event": True, "decision_boundary": "只保存ChatGPT已经形成的正式决策和决策时点可见证据。禁止使用决策时点之后的行情回填价格或比较快照；研究留痕用于验证候选选择、假设生命周期、判断与执行质量，不自行推导交易权限。", "recorded_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
    event_path = ROOT / "events/decisions" / f"{decision_id}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    if event_path.exists():
        prior = load_json(event_path)
        if prior.get("fingerprint") == fingerprint and prior.get("price_source_snapshot") == snapshot_rel:
            return True, decision_id
    atomic_json_write(event_path, event)
    return True, decision_id


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


def record_post_close_review(account: dict, request: dict) -> tuple[bool, bool]:
    review = request.get("formal_review")
    if request.get("interaction_scenario") != "POST_CLOSE_REVIEW" or not review:
        return False, False
    market_date = str(review.get("market_date") or request.get("market_date") or account.get("last_confirmed_market_date") or "")
    if not market_date:
        raise RuntimeError("POST_CLOSE_REVIEW requires market_date")
    payload = {"market_date": market_date, "account_updated_at": account.get("updated_at"), "formal_review": review}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    event_path = ROOT / "events" / "reviews" / f"{market_date}.json"
    prior = load_json(event_path) if event_path.exists() else {}
    if prior.get("fingerprint") == fingerprint:
        return True, True
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
    if prior_time and incoming_time and incoming_time < prior_time:
        return True, True
    review_time = (incoming_time or datetime.now(SHANGHAI)).isoformat(timespec="seconds")
    event = {"event_type": "FORMAL_POST_CLOSE_REVIEW", "market_date": market_date, "account_updated_at": account.get("updated_at"), "fingerprint": fingerprint, "request_id": request.get("request_id"), "review": review, "updated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
    event["reviewed_at_beijing"] = review_time
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(event_path, event)
    archive_entry = str(review.get("archive_entry") or "").strip()
    if archive_entry:
        upsert_formal_line(ROOT, ARCHIVE.name, REVIEW_ARCHIVE_START, REVIEW_ARCHIVE_END, market_date, archive_entry, before_heading="## 6. 历史Excel与专项数据来源")
    experience_entry = str(review.get("experience_entry") or "").strip()
    if experience_entry:
        case_mode = str(review.get("case_mode") or "").upper()\n        if case_mode.startswith("NEW_CASE_FROM_EXECUTED_") and str(review.get("case_id") or "").strip():
            case_entry = experience_entry
            if case_entry.startswith("### "):
                case_entry = "#### " + case_entry[4:]
            upsert_managed_line(ROOT, EXPERIENCE.name, CASE_DETAILS_START, CASE_DETAILS_END, str(review.get("case_id")), case_entry, before_heading="## 3. 历史研究与专项回测")
        else:
            upsert_formal_line(ROOT, EXPERIENCE.name, REVIEW_EXPERIENCE_START, REVIEW_EXPERIENCE_END, market_date, experience_entry, before_heading="## 5. 研究与经验转化")
    record_close_review_closure(account, request, review, event)
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
    dprice = safe_float(decision.get("price_at_decision"))
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
    return {"status": "READY" if dprice is not None and eprice is not None and (delay is None or delay >= 0 or bounded_before_execution) else "PARTIAL", "decision_id": linked_decision_id, "hypothesis_id": decision.get("hypothesis_id"), "decision_price": dprice, "execution_price": eprice, "execution_price_vs_decision_pct": diff_pct, "adverse_execution_cost_pct": adverse, "decision_to_execution_seconds": delay if delay is None or delay >= 0 else None, "decision_effective_ordering": decision.get("decision_effective_ordering"), "timing_quality": decision.get("timing_quality"), "timing_status": "EXACT" if delay is not None and delay >= 0 else "BOUNDED_BEFORE_EXECUTION" if bounded_before_execution else "UNAVAILABLE", "method_note": "正的adverse_execution_cost_pct表示相对正式决策价格出现不利执行偏差；买入价更高或卖出价更低均为正。该指标分离判断质量与执行质量，不改变交易权限。"}



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
    fee = safe_float(event.get("fee_amount", event.get("fee")))
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
    lifecycle = str(event.get("lifecycle") or "待确认")
    linked = str(event.get("linked_decision_id") or "")
    note = lifecycle + (f"；关联决策{linked}" if linked else "") + "；真实成交已执行"
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
    write_formal_text_if_changed(ROOT, EXPERIENCE.name, text)

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


def is_broker_screenshot_request(request: dict) -> bool:
    return (
        str(request.get("source") or "").upper() == "CHATGPT_USER_BROKER_SCREENSHOT"
        or str(request.get("interaction_scenario") or "").upper() == "BROKER_SCREENSHOT_SYNC"
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
    trade = request.get("trade_event")
    prior_account = load_json(ACCOUNT) if ACCOUNT.exists() else {}
    supplied_account = request.get("account_fact")
    account_sync_status = "NOT_APPLICABLE"
    if not supplied_account and isinstance(trade, dict):
        supplied_account = _apply_trade_to_account(prior_account, trade)
    if is_broker_screenshot_request(request) and not isinstance(supplied_account, dict) and not isinstance(trade, dict):
        result = {
            "ok": False,
            "request_id": request.get("request_id"),
            "interaction_scenario": request.get("interaction_scenario"),
            "status": "NO_ACCOUNT_FACT",
            "account_sync_status": "ACCOUNT_SYNC_NOT_PERFORMED",
            "dashboard_updated": False,
            "detail": "Broker screenshot request has no request-scoped account_fact; market success must not be treated as account sync success.",
        }
        print(json.dumps(result, ensure_ascii=False))
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
        confirmed_at = trade.get("confirmed_at_beijing") or account.get("updated_at")
        idempotency_key = _trade_idempotency_key(trade, confirmed_at)
        existing = _find_existing_trade(trade, confirmed_at, idempotency_key)
        if existing:
            event, event_id, trade_event_recorded = existing, str(existing.get("event_id") or ""), True
        else:
            event_id = str(trade.get("event_id") or request.get("request_id") or datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S"))
            linked_decision_id = str(trade.get("decision_id") or decision_id or "")
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
    dashboard = replace_block(DASHBOARD.read_text(encoding="utf-8"), START, END, build_dashboard_block(account, latest_formal_review_decision(ROOT) or request.get("formal_decision"), request), insert_after_heading=True)
    write_formal_text_if_changed(ROOT, DASHBOARD.name, dashboard)
    # Keep the three human-readable fact documents synchronized even when the
    # request only confirms a fee/account snapshot and creates no new trade event.
    formal_files_sync = sync_formal_files(ROOT, account)
    result = {"ok": True, "request_id": request.get("request_id"), "interaction_scenario": request.get("interaction_scenario"), "account_updated_at": account.get("updated_at"), "dashboard_updated": True, "formal_decision_recorded": decision_recorded, "formal_decision_id": decision_id, "trade_event_recorded": trade_event_recorded, "post_close_review_recorded": review_recorded, "post_close_review_idempotent_noop": review_idempotent, "review_prerequisite_unavailable_recorded": unavailable_recorded, "review_prerequisite_unavailable_idempotent_noop": unavailable_idempotent, "formal_files_sync": formal_files_sync, "account_sync_status": account_sync_status}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
