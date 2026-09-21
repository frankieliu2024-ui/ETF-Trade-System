from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
    from build_stock_context import active_account_asset_codes
    from market_quote_router import build_market_quote_context
    from formal_etf_opportunity_discovery import attach_formal_quotes, discover_formal_candidates
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
    from scripts.build_stock_context import active_account_asset_codes
    from scripts.market_quote_router import build_market_quote_context
    from scripts.formal_etf_opportunity_discovery import attach_formal_quotes, discover_formal_candidates

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

CANONICAL_FILES = {
    "index": "ETF_SYSTEM_INDEX.md", "master": "ETF规则_MASTER.md", "dashboard": "ETF当前状态_DASHBOARD.md",
    "experience": "ETF交易复盘与经验库_2026.md", "market_archive": "ETF市场行情档案_2026.md",
    "data_standard": "ETF与市场监测数据接口使用规范.md", "current": "data/state/CURRENT.json",
    "account_fact": "data/state/account_fact.json", "asset_roles": "data/state/asset_roles.json",
    "etf_monitor_universe": "config/market/etf_monitor_universe.json", "trading_calendar": "config/market/a_share_trading_calendar_2026.json",
    "stock_context": "data/state/stock_context.json", "stock_market_context": "data/state/stock_market_context.json",
    "stock_monitor_policy": "config/market/stock_monitor_policy.json", "market_delta": "data/state/market_delta.json",
    "overseas_context": "data/state/overseas_context.json", "us_extended_hours_context": "data/state/us_extended_hours_context.json",
    "runtime_health": "data/state/runtime_health.json", "runtime_policy": "config/runtime_policy.json",
    "system_consistency": "data/state/system_consistency.json", "market_quote_router": "scripts/market_quote_router.py", "market_quote_router_config": "config/market/market_quote_router.json",
}


def parse_time(text: str) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate_freshness(current: dict, policy: dict) -> dict:
    captured = parse_time(current.get("captured_at", ""))
    now = datetime.now(timezone.utc)
    if captured is None:
        return {"status": "STALE", "age_seconds": None, "reason": "missing_captured_at"}
    age = max(0, int((now - captured).total_seconds()))
    fresh_max = int(policy.get("fresh_max_age_seconds", 900))
    degraded_max = int(policy.get("degraded_max_age_seconds", 1500))
    status = "FRESH" if age <= fresh_max else ("DEGRADED" if age <= degraded_max else "STALE")
    return {"status": status, "age_seconds": age, "fresh_max_age_seconds": fresh_max, "degraded_max_age_seconds": degraded_max}


def current_trading_day_status(calendar: dict) -> dict:
    now = datetime.now(SHANGHAI)
    date_text = now.date().isoformat()
    start, end = str(calendar.get("coverage_start", "")), str(calendar.get("coverage_end", ""))
    covered = bool(start and end and start <= date_text <= end)
    weekend = now.weekday() >= 5
    exchange_closed = date_text in set(calendar.get("closed_dates") or [])
    return {"market_date": date_text, "calendar_covered": covered, "weekend": weekend, "exchange_closed": exchange_closed, "is_candidate_trading_day": covered and not weekend and not exchange_closed, "calendar_source": calendar.get("source", {})}


def account_gate_status(current: dict, account: dict, policy: dict) -> dict:
    raw_valid = account.get("status") == "VALID"
    market_date = current.get("market_date", "")
    updated = parse_time(account.get("updated_at", ""))
    updated_market_date = updated.astimezone(SHANGHAI).date().isoformat() if updated else ""
    same_day_required = bool(policy.get("account_fact_same_market_date_required", False))
    same_day = bool(market_date and updated_market_date == market_date)
    usable = raw_valid and (same_day or not same_day_required)
    reason = "OK" if usable else ("ACCOUNT_NOT_VALID" if not raw_valid else "ACCOUNT_FACT_NOT_CURRENT_MARKET_DATE")
    return {"raw_status": account.get("status", "MISSING"), "updated_at": account.get("updated_at", ""), "updated_market_date": updated_market_date, "current_market_date": market_date, "same_market_date_required": same_day_required, "can_use_current_account_fact": usable, "requires_user_broker_screenshot": not usable, "reason": reason, "rule": "正式盘中/盘后决策使用的账户、持仓、现金和成交事实必须符合当前账户事实有效性机制；已确认账户变化后必须刷新，未确认变化时按EVENT_DRIVEN_CARRY_FORWARD处理。"}


def build_read_plan(current: dict, account: dict, policy: dict, freshness: dict) -> dict:
    latest_snapshot = current.get("latest_snapshot", "")
    account_gate = account_gate_status(current, account, policy)
    effective_data_status = {**(current.get("data_freshness") or {}), **freshness}
    required = [CANONICAL_FILES[k] for k in ["index", "master", "data_standard", "current", "system_consistency", "runtime_policy", "runtime_health", "trading_calendar", "etf_monitor_universe", "overseas_context", "us_extended_hours_context", "stock_monitor_policy", "stock_context", "stock_market_context"]]
    if latest_snapshot:
        required.append(latest_snapshot)
    required.extend([CANONICAL_FILES["market_delta"], CANONICAL_FILES["dashboard"]])
    if account_gate["can_use_current_account_fact"]:
        required.extend([CANONICAL_FILES["account_fact"], CANONICAL_FILES["asset_roles"]])

    return {
        "mode": "QUERY_TIME_REFRESH_FIRST",
        "acquisition_priority": [
            "QUERY_TIME_IMMEDIATE_REFRESH",
            "LATEST_VALID_SNAPSHOT",
            "EXPLICIT_DATA_LIMIT_OR_MISSING",
        ],
        "acquisition_priority_rule": "正式盘前、集合竞价、盘中、收盘即时检查及用户上传券商截图后的分析，先尝试查询时立即补采；只有补采失败、超时、限流、对象不可得或场景不允许补采时，才回退最近有效快照。部分对象补采成功时逐对象使用最新有效证据，不为统一时点整体退回旧快照。",
        "required_reads": required,
        "conditional_reads": {
            "lifecycle_or_prior_case_needed": CANONICAL_FILES["experience"],
            "historical_market_fact_needed": CANONICAL_FILES["market_archive"],
            "industry_chain_stock_needed": "按当前ETF/行业假设临时发现跨市场最有解释力的产业链公司；美股个股可同时核验REGULAR/POST_MARKET/PRE_MARKET，但不使用永久固定名单。",
        },
        "account_gate": account_gate,
        "market_gate": {
            "node_status": current.get("node_status", ""), "latest_valid_node": current.get("latest_valid_node", ""), "latest_snapshot": latest_snapshot,
            "captured_at": current.get("captured_at", ""), "captured_at_beijing": current.get("data_freshness", {}).get("captured_at_beijing", current.get("captured_at", "")),
            "market_delta": CANONICAL_FILES["market_delta"], "overseas_context": CANONICAL_FILES["overseas_context"], "us_extended_hours_context": CANONICAL_FILES["us_extended_hours_context"],
            "etf_monitor_universe": CANONICAL_FILES["etf_monitor_universe"], "trading_calendar": CANONICAL_FILES["trading_calendar"],
            "stock_context": CANONICAL_FILES["stock_context"], "stock_market_context": CANONICAL_FILES["stock_market_context"],
            "system_consistency": CANONICAL_FILES["system_consistency"], "data_standard": CANONICAL_FILES["data_standard"], "runtime_health": CANONICAL_FILES["runtime_health"], "runtime_policy": CANONICAL_FILES["runtime_policy"],
            "freshness_at_context_build": freshness, "data_freshness": effective_data_status,
            "query_time_rule": "行情获取固定顺序为：查询时立即补采 → 最近一次有效快照 → 明确降级/缺失。最近快照只有在即时补采失败或不可执行时才作为第二顺位；使用前必须按查询时刻重新判断为“时点正常／存在延迟／时点过旧”。",
            "partial_refresh_rule": "逐对象采用最新有效证据：补采成功对象使用新数据，失败对象才回退最近有效快照；不得为了统一时点把成功补采对象整体退回旧快照。",
            "broker_screenshot_rule": "券商截图只确定账户、持仓、现金和成交事实；收到截图后行情仍应优先重新补采。截图时间不得冒充ETF、指数或个股行情时间。",
            "output_time_rule": "任何正式行情分析、ETF判断、盘中复核或盘后复盘，只要引用行情，必须显式输出【数据时点（北京时间）】。A股使用实际provider/capture时点；海外/亚洲对象优先使用latest.as_of_beijing；美股扩展时段同时标注PRE_MARKET/REGULAR/POST_MARKET。多个对象时点明显不一致时分别标注。",
            "delay_visibility_rule": "若数据相对查询时刻存在可见延迟，不隐藏延迟；直接展示北京时间as_of，并在必要时注明距当前约多少分钟。",
            "trading_day_rule": "每次当前查询先读取A股官方交易日历，区分正常交易日前/盘中/盘后、周末与交易所休市。",
            "consistency_rule": "正式分析前读取system_consistency.json；硬FAIL先处理系统冲突。",
            "data_standard_rule": "行情来源、质量、查询时补采优先级、盘前/盘中脉冲、新鲜度、跨市场时点和降级边界以一级目录数据规范为基础。",
            "etf_rule": "ETF持续监测以etf_monitor_universe.json为唯一运行清单；持仓/观察身份由Dashboard和账户事实解释。正式查询节点可按MASTER使用广域只读发现，但池外对象必须经现有对象级正式补采后才可进入本节点完整评估，且不得自动写入持续监测清单。",
            "overseas_rule": "正式海外/亚洲指数必须检查NDX、SOX、N225、KOSPI、TWII、HSTECH；北京时间08:00起已有日韩市场脉冲，不能等A股9:30才开始读取海外。",
            "us_extended_hours_rule": "美国信息分三段解释：上一正式现金盘（NDX/SOX）、POST_MARKET（QQQ/SOXX及条件个股）、下一交易日PRE_MARKET。A股早盘前可能获得上一美股盘后信息；下一美股PRE_MARKET通常在北京时间A股收盘后开始，主要形成下一A股交易日的前置信号。扩展时段不得等同正式指数确认。",
            "stock_rule": "第三层默认个股由当前有效账户事实动态生成；产业链个股按查询主题动态发现。",
            "rule": "正式当前查询优先补采当前可得行情，再使用最近有效状态补充连续性；数据不足时明确不足。",
        },
        "runtime_resilience": {"target_cadence_seconds": policy.get("target_cadence_seconds", 600), "fresh_max_age_seconds": policy.get("fresh_max_age_seconds", 900), "degraded_max_age_seconds": policy.get("degraded_max_age_seconds", 1500), "close_grace_seconds": policy.get("close_grace_seconds", 900), "principle": "查询时立即补采优先；常规10分钟只是生产目标。补采失败才回退最近有效状态，并按查询时刻重新判定新鲜度。"},
    }




def _duration_seconds(start: str, end: str) -> float | None:
    first, second = parse_time(start), parse_time(end)
    if first is None or second is None:
        return None
    return round(max(0.0, (second - first).total_seconds()), 3)


def _stable_manual_request_identity(request: dict) -> str:
    """Return a deterministic identity for an existing durable manual request.

    The identity is diagnostic-only.  Prefer a request_id already carried by the
    durable request object; otherwise derive a stable identity from the existing
    request path plus its observed request timestamp.  Missing inputs remain
    explicit UNKNOWN rather than being inferred from product-side timing.
    """
    explicit = str(request.get("request_id") or request.get("manual_request_identity") or "").strip()
    if explicit:
        return explicit
    request_path = str(
        request.get("_request_file")
        or request.get("_ingress_path")
        or request.get("request_file")
        or request.get("consumed_external_market_evidence")
        or ""
    ).strip()
    requested_at = str(request.get("requested_at_beijing") or request.get("request_time") or "").strip()
    if not request_path or not requested_at:
        return "UNKNOWN"
    normalized_path = request_path.replace("\\", "/")
    if normalized_path.startswith("/") or ".." in Path(normalized_path).parts:
        return "UNKNOWN"
    digest = hashlib.sha256(f"{normalized_path}|{requested_at}".encode("utf-8")).hexdigest()[:12]
    stem = Path(normalized_path).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-") or "manual_request"
    return f"{safe_stem}-{digest}"


def build_decision_fact_pack(root: Path, request: dict, current: dict, account: dict, decision: dict, market_quote: dict, formal_discovery: dict | None = None) -> dict:
    """Project one request's minimum sufficient, decision-ready PIT inputs.

    This remains an in-memory read-only projection.  It normalizes already
    qualified facts and completion obligations; it never ranks candidates or
    derives a buy/sell/capital-allocation answer.
    """
    freshness = market_quote.get("decision_freshness") or {}
    universe = read_json(root / CANONICAL_FILES["etf_monitor_universe"], {})
    discovery = formal_discovery or {}
    positions = []
    for item in account.get("positions") or []:
        if not isinstance(item, dict) or float(item.get("quantity") or 0) <= 0:
            continue
        positions.append({
            "code": str(item.get("code") or item.get("symbol") or item.get("security_code") or ""),
            "name": item.get("name") or "",
            "asset_type": item.get("asset_type") or "",
            "quantity": item.get("quantity"),
            "market_value": item.get("market_value"),
            "cost": item.get("cost") or item.get("cost_price"),
        })

    quotes = []
    for item in market_quote.get("quotes") or []:
        if not isinstance(item, dict):
            continue
        quotes.append({
            "code": str(item.get("symbol") or item.get("code") or item.get("object_code") or ""),
            "name": item.get("name") or item.get("object_name") or "",
            "price": item.get("price") if item.get("price") is not None else item.get("close"),
            "change_pct": item.get("change_pct"),
            "market_phase": item.get("market_phase") or "",
            "as_of_beijing": item.get("data_time_beijing") or item.get("as_of_beijing") or item.get("provider_as_of") or "",
            "quality_status": item.get("quality_status") or item.get("freshness") or "",
            "provider": item.get("provider") or item.get("provider_used") or "",
        })

    discovery_inputs = []
    for item in discovery.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        discovery_inputs.append({
            "code": str(item.get("code") or ""),
            "name": item.get("name") or "",
            "eligibility": item.get("eligibility") or item.get("status") or "",
            "evidence": item.get("evidence") or item.get("features") or {},
        })

    request_id = str(request.get("request_id") or "").strip()
    requested_at = _request_received_at_beijing(request)
    request_source = str(request.get("source") or request.get("requested_by") or "").strip().upper()
    manual_formal_sources = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "CHATGPT_USER_CONTINUE",
        "CHATGPT_USER_GITHUB_INTRADAY",
        "CHATGPT_USER_REQUEST",
        "CHATGPT_USER_INTERACTION",
    }
    manual_formal_intents = {"FORMAL_INTRADAY_DECISION", "FORMAL_INTRADAY_ANALYSIS", "EXPLICIT_LATEST"}
    request_intent = str(request.get("intent") or request.get("query_intent") or "").strip().upper()
    is_manual_formal_request = request_source in manual_formal_sources and request_intent in manual_formal_intents
    ingress_path = str(request.get("_request_file") or request.get("_ingress_path") or request.get("request_file") or "").strip().replace("\\", "/")
    ingress_path_parts = Path(ingress_path).parts if ingress_path else ()
    canonical_source_ingress = bool(
        ingress_path
        and not ingress_path.startswith("/")
        and ".." not in ingress_path_parts
        and len(ingress_path_parts) >= 3
        and ingress_path_parts[0] == "requests"
        and ingress_path_parts[1] == "live_snapshot"
        and ingress_path.endswith(".json")
    )
    manual_source_ingress_qualified = (not is_manual_formal_request) or canonical_source_ingress
    discovery_status = str(discovery.get("status") or "NOT_REQUESTED").strip().upper()
    discovery_inflight = discovery_status in {"PENDING", "RUNNING", "QUEUED", "IN_PROGRESS", "BUILDING"}
    discovery_resolved = discovery_status not in {"", "NOT_REQUESTED", "PENDING", "RUNNING", "QUEUED", "IN_PROGRESS", "BUILDING", "UNKNOWN"}
    post_request = bool(freshness.get("resolved_post_request") or freshness.get("post_request"))
    fallback_available = bool(freshness.get("fallback_allowed"))
    account_ready = str(account.get("status") or "").upper() == "VALID"

    # Formal reply-freeze is stricter than analysis availability.  A manual
    # request may legally degrade after the canonical chain reaches a terminal
    # failure/unavailable outcome, but it must not freeze against an older
    # context while the same-request decision facts are still forming.
    request_scoped_context = bool(
        is_manual_formal_request
        and manual_source_ingress_qualified
        and request_id
        and requested_at
    )
    pit_inflight = request_scoped_context and not post_request and not fallback_available
    reply_freeze_blockers = []
    if request_scoped_context and pit_inflight:
        reply_freeze_blockers.append("REQUEST_SCOPED_PIT_IN_FLIGHT")
    if request_scoped_context and discovery_inflight:
        reply_freeze_blockers.append("FORMAL_DISCOVERY_IN_FLIGHT")
    formal_reply_freeze = {
        "status": "IN_FLIGHT" if reply_freeze_blockers else (
            "READY" if request_scoped_context and post_request else
            "RESOLVED_DEGRADED" if request_scoped_context else
            "NOT_APPLICABLE"
        ),
        "reply_freezable": not reply_freeze_blockers,
        "blockers": reply_freeze_blockers,
        "request_id": request_id,
        "post_request_pit_resolved": post_request,
        "discovery_status": discovery_status,
        "rule": "Manual formal replies must not freeze while same-request decision facts are still in flight. Explicit terminal degradation may continue legal reasoning; Discovery success is not required.",
    }

    ingress_blockers = []
    if not request_id:
        ingress_blockers.append("REQUEST_IDENTITY_MISSING")
    if not requested_at:
        ingress_blockers.append("REQUEST_TIME_MISSING")
    if is_manual_formal_request and not manual_source_ingress_qualified:
        ingress_blockers.append("MANUAL_SOURCE_INGRESS_UNQUALIFIED")

    analysis_limitations = []
    if not post_request:
        analysis_limitations.append(
            "REQUEST_SCOPED_PIT_NOT_RESOLVED_FALLBACK_AVAILABLE"
            if fallback_available else "CURRENT_MARKET_FACT_NOT_ACTION_QUALIFIED"
        )
    if not account_ready:
        analysis_limitations.append("ACCOUNT_FACT_NOT_READY_EXACT_AMOUNT_SHARE_BLOCKED")
    if not discovery_resolved:
        analysis_limitations.append("FORMAL_DISCOVERY_NOT_RESOLVED")

    formal_analysis_availability = {
        "status": "AVAILABLE" if not ingress_blockers and not analysis_limitations else (
            "DEGRADED" if not ingress_blockers else "BLOCKED"
        ),
        "available": not ingress_blockers,
        "global_blockers": ingress_blockers,
        "limitations": analysis_limitations,
        "source_ingress": {
            "manual_formal_request": is_manual_formal_request,
            "qualified": manual_source_ingress_qualified,
            "path": ingress_path,
            "rule": "Manual formal analysis is request-scoped only when bound to the existing canonical requests/live_snapshot ingress. Derived state alone cannot impersonate a new manual formal request.",
        },
        "rule": "Missing market/account/discovery facts localize their impact and do not suppress independent legal analysis. Missing canonical request identity/time or an unqualified manual source ingress globally blocks this request-scoped formal-analysis packet.",
    }

    formal_analysis_availability["reply_freeze"] = formal_reply_freeze

    action_blockers = list(ingress_blockers)
    if not post_request:
        action_blockers.append("REQUEST_SCOPED_PIT_NOT_RESOLVED")
    if not account_ready:
        action_blockers.append("ACCOUNT_FACT_NOT_READY")
    formal_action_readiness = {
        "status": "READY" if not action_blockers else "NOT_READY",
        "ready": not action_blockers,
        "blockers": action_blockers,
        "request_bound": bool(request_id and requested_at),
        "request_scoped_pit_resolved": post_request,
        "fallback_available": fallback_available,
        "account_fact_ready": account_ready,
        "rule": "Exact amount/share/action canonical completion remains fail-closed on request identity, action-qualified request-scoped PIT and valid account truth. This gate must not be interpreted as a ban on independent legal analysis.",
    }

    formal_reasoning_readiness = {
        "status": "READY" if formal_analysis_availability["available"] else "NOT_READY",
        "ready": formal_analysis_availability["available"],
        "blockers": ingress_blockers,
        "limitations": analysis_limitations,
        "request_bound": bool(request_id and requested_at),
        "request_scoped_pit_resolved": post_request,
        "account_fact_ready": account_ready,
        "formal_discovery_status": discovery_status,
        "formal_discovery_resolved": discovery_resolved,
        "capital_efficiency_scope": "FULL_MARKET" if discovery_resolved else "DEGRADED_KNOWN_UNIVERSE",
        "capital_efficiency_limitations": [] if discovery_resolved else ["FORMAL_DISCOVERY_NOT_RESOLVED"],
        "compatibility_role": "ANALYSIS_AVAILABILITY_NOT_ACTION_QUALIFICATION",
        "rule": "Formal reasoning continues under explicit local limitations. Exact executable/canonical action qualification is owned by formal_action_readiness.",
    }

    return {
        "schema_version": "1.4",
        "role": "PREFERRED_MINIMUM_SUFFICIENT_FORMAL_REASONING_INPUT",
        "trigger": {
            "source": request.get("requested_by") or request.get("source") or "INTERACTIVE_QUERY",
            "request_id": request.get("request_id") or "",
            "requested_at_beijing": _request_received_at_beijing(request),
        },
        "master": {
            "version": decision.get("rules_version") or current.get("rules_version") or "",
            "source": "ETF规则_MASTER.md",
        },
        "account_fact": {
            "source": account.get("source") or "",
            "as_of_beijing": account.get("updated_at") or "",
            "status": account.get("status") or "",
            "deployable_cash": account.get("deployable_cash"),
            "reserved_cash_for_settlement": account.get("reserved_cash_for_settlement"),
            "positions": positions,
        },
        "current": {
            "market_date": current.get("market_date") or "",
            "market_phase": current.get("market_phase") or "",
            "latest_snapshot": current.get("latest_snapshot") or "",
            "captured_at_beijing": current.get("captured_at") or (current.get("data_freshness") or {}).get("captured_at_beijing") or "",
            "provider_as_of_beijing": (current.get("data_freshness") or {}).get("provider_as_of") or "",
            "provider": (current.get("data_freshness") or {}).get("provider") or "",
        },
        "qualified_market_facts": quotes,
        "decision_context": {
            "generated_at_beijing": decision.get("generated_at_beijing") or decision.get("generated_at") or "",
            "source": "scripts/state_manager.py::build_decision_context",
            "analysis_coverage": decision.get("analysis_coverage") or {},
            "lifecycle_projection": decision.get("lifecycle_projection") or {},
            "risk_metrics": decision.get("etf_strategy_risk_metrics") or {},
        },
        "market_quote": {
            "mode": market_quote.get("refresh_mode") or "",
            "decision_freshness": freshness,
            "quotes_as_of_beijing": sorted({str(q.get("data_time_beijing") or q.get("as_of_beijing") or "") for q in (market_quote.get("quotes") or []) if isinstance(q, dict) and (q.get("data_time_beijing") or q.get("as_of_beijing"))}),
            "source": "scripts/market_quote_router.py",
        },
        "etf_universe": {
            "source": CANONICAL_FILES["etf_monitor_universe"],
            "version": universe.get("version") or "",
            "count": len(universe.get("objects") or []),
        },
        "formal_analysis_availability": formal_analysis_availability,
        "formal_reply_freeze": formal_reply_freeze,
        "formal_action_readiness": formal_action_readiness,
        "formal_reasoning_readiness": formal_reasoning_readiness,
        "opportunity_inputs": {
            "formal_discovery_status": discovery.get("status") or "NOT_REQUESTED",
            "candidates": discovery_inputs,
            "rule": "Inputs only. Candidate selection and capital allocation remain ChatGPT judgments under MASTER.",
        },
        "formal_reasoning_obligations": [
            "THREE_LAYER_MONITORING",
            "ALL_OBSERVATION_AND_HELD_ETF_OPPORTUNITY_COMPARISON",
            "EVERY_ACTUAL_POSITION_MANAGED_POSITION_REVIEW",
            "EVERY_ACTUAL_POSITION_HOLD_REDUCE_EXIT_SYMMETRIC_CAPITAL_COMPETITION",
            "HELD_ETF_ADDITIONAL_CAPITAL_REVIEW_WHERE_APPLICABLE",
            "RELEASABLE_LOW_EFFICIENCY_CAPITAL_REVIEW",
            "EXPLICIT_NEXT_UNIT_CAPITAL_USE",
            "ZERO_AMOUNT_ONLY_AFTER_FULL_CAPITAL_COMPETITION",
            "SYMMETRIC_CASH_VS_ALL_LEGAL_CAPITAL_STATES_COMPARISON",
            "POST_ACTION_CASH_AND_FUTURE_TRIAL_CONFIRM_CAPACITY",
            "CONCENTRATION_AND_ACCOUNT_STRUCTURE_EFFECT",
            "CAPITAL_SOURCE_OR_DESTINATION_FOR_RELEASE_OR_MIGRATION",
        ],
        "conditional_reads": {
            "experience": CANONICAL_FILES["experience"],
            "market_archive": CANONICAL_FILES["market_archive"],
            "rule": "Read only when the evidence can change risk permission, opportunity, lifecycle, amount, funding source, sell action, account truth, or capital allocation.",
        },
        "deferred_non_blocking": [
            "DASHBOARD_RENDER",
            "MARKET_ARCHIVE_RENDER",
            "REVIEW_CONTEXT",
            "NOTIFICATION_RENDER",
            "NON_DECISION_E2E_PROJECTION",
        ],
        "provenance_rule": "Every projected fact is copied from the existing canonical request/account/current/decision/quote/discovery inputs; this packet is not a new fact owner.",
        "pit_rule": "Formal reasoning may continue when formal_analysis_availability is AVAILABLE or DEGRADED, honoring local limitations. Exact amount/share/action canonical completion requires formal_action_readiness=READY. Downstream projections cannot mutate the same PIT decision.",
        "decision_boundary": "This packet normalizes facts and obligations only. It must not select the main candidate, rank capital states, or generate buy/sell actions.",
    }


def _first_qualified_market_fact(market_quote: dict) -> dict:
    for quote in market_quote.get("quotes") or []:
        if not isinstance(quote, dict):
            continue
        quality = str(quote.get("quality_status") or quote.get("freshness") or "").upper()
        as_of = quote.get("data_time_beijing") or quote.get("as_of_beijing") or quote.get("provider_as_of") or ""
        if as_of and quality not in {"", "MISSING", "STALE", "DATA_UNAVAILABLE", "INVALID"}:
            identity = (
                quote.get("identity")
                or quote.get("quote_identity")
                or quote.get("object_code")
                or quote.get("code")
                or quote.get("symbol")
                or quote.get("name")
                or ""
            )
            return {
                "identity": str(identity),
                "as_of_beijing": as_of,
                "quality_status": quality,
            }
    return {"identity": "UNKNOWN", "as_of_beijing": "UNKNOWN", "quality_status": "UNKNOWN"}


def _request_received_at_beijing(request: dict) -> str:
    """Return the real request timestamp in one display timezone when supplied.

    UTC ingress timestamps are converted; missing timestamps remain explicit and
    are never inferred from a filename or workflow time.
    """
    value = str(request.get("requested_at_beijing") or request.get("request_time") or "").strip()
    if value:
        return value
    utc_value = str(request.get("requested_at_utc") or "").strip()
    if not utc_value:
        return ""
    try:
        dt = datetime.fromisoformat(utc_value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SHANGHAI).isoformat(timespec="seconds")


def _minimum_legal_inputs_ready_at(root: Path, request: dict, current: dict, account: dict, market_quote: dict, observed_at: str) -> str:
    """Return the earliest repository-observed time when action-determinative inputs are present.

    This is diagnostic/readiness metadata only. It does not authorize a trade,
    replace formal analysis, or relax any freshness/account/sell-chain contract.
    """
    explicit = str(request.get("minimum_legal_inputs_ready_at_beijing") or "").strip()
    if explicit:
        return explicit

    freshness = market_quote.get("decision_freshness") or {}
    post_request_current = bool(freshness.get("resolved_post_request") or freshness.get("post_request"))
    if not post_request_current:
        return "UNKNOWN"

    current_time = (
        str(current.get("captured_at") or "").strip()
        or str((current.get("data_freshness") or {}).get("captured_at_beijing") or "").strip()
    )
    if not current_time:
        return "UNKNOWN"

    if str(account.get("status") or "").upper() != "VALID":
        return "UNKNOWN"

    held_stocks = [
        str(item.get("code") or "").strip()
        for item in (account.get("positions") or [])
        if isinstance(item, dict)
        and str(item.get("asset_type") or "").upper() == "STOCK"
        and float(item.get("quantity") or 0) > 0
        and str(item.get("code") or "").strip()
    ]
    if held_stocks:
        stock_ctx = read_json(root / CANONICAL_FILES["stock_market_context"], {})
        objects = stock_ctx.get("objects") or stock_ctx.get("stocks") or {}
        if isinstance(objects, list):
            indexed = {
                str(item.get("code") or item.get("symbol") or "").strip(): item
                for item in objects
                if isinstance(item, dict)
            }
        elif isinstance(objects, dict):
            indexed = objects
        else:
            indexed = {}
        for code in held_stocks:
            row = indexed.get(code) or {}
            if str(row.get("quality_status") or "").upper() not in {"PASS", "DEGRADED"}:
                return "UNKNOWN"
            as_of = str(row.get("as_of_beijing") or row.get("data_time_beijing") or "").strip()
            if not as_of:
                return "UNKNOWN"

    return observed_at or current_time


def build_fast_path_latency(request: dict, current: dict, account: dict, decision: dict, market_quote: dict, reply_ready: str, root: Path | None = None) -> dict:
    """Report only observed timestamps; missing instrumentation stays explicit."""
    t0 = _request_received_at_beijing(request)
    manual_request_identity = _stable_manual_request_identity(request)
    freshness = market_quote.get("decision_freshness") or {}
    t_new = current.get("captured_at") or (current.get("data_freshness") or {}).get("captured_at_beijing") or ""
    t_decision = decision.get("generated_at_beijing") or decision.get("generated_at") or ""
    t_refresh = request.get("refresh_started_at_beijing") or request.get("refresh_reused_at_beijing") or ""
    first_market_fact = _first_qualified_market_fact(market_quote)
    final_identity = request.get("final_answer_identity") or request.get("final_analysis_identity") or reply_ready or "UNKNOWN"
    minimum_ready = _minimum_legal_inputs_ready_at(root or ROOT, request, current, account, market_quote, reply_ready)
    return {
        "t0": t0,
        "manual_request_identity": manual_request_identity,
        "manual_request_received_at": t0 or "UNKNOWN",
        "user_request_received": t0,
        "screenshot_account_fact_available": request.get("screenshot_account_fact_available_at_beijing") or request.get("account_fact_available_at_beijing") or "UNKNOWN",
        "screenshot_account_fact_available_at": request.get("screenshot_account_fact_available_at_beijing") or request.get("account_fact_available_at_beijing") or "UNKNOWN",
        "t_refresh_start_or_reuse": t_refresh,
        "market_refresh_identity": request.get("market_refresh_identity") or request.get("refresh_request_id") or "UNKNOWN",
        "first_qualified_market_fact_identity": first_market_fact["identity"],
        "first_qualified_market_fact_as_of": first_market_fact["as_of_beijing"],
        "first_qualified_market_fact_quality": first_market_fact["quality_status"],
        "t_new_current": t_new if freshness.get("resolved_post_request") or freshness.get("post_request") else "",
        "t_minimum_legal_inputs_ready": minimum_ready,
        "minimum_legal_inputs_ready_at": minimum_ready,
        "t_decision_ready": t_decision,
        "final_answer_identity": final_identity,
        "final_analysis_identity": final_identity,
        "t_reply_or_output_ready": reply_ready,
        "minimum_legal_inputs_ready_role": "NON_TERMINAL_READINESS_MARKER",
        "minimum_legal_inputs_ready_is_stop_boundary": False,
        "canonical_chain_status": "FORMAL_COMPLETION_PENDING",
        "canonical_chain_next_step": "CONTINUE_EXISTING_CANONICAL_CHAIN",
        "required_account_persistence_identity": request.get("required_account_persistence_identity") or "UNKNOWN",
        "refresh_start_latency": _duration_seconds(t0, t_refresh),
        "refresh_duration": _duration_seconds(t_refresh, t_new),
        "post_current_decision_latency": _duration_seconds(t_new, t_decision),
        "total_fast_path_latency": _duration_seconds(t0, reply_ready),
        "measurement_status": "OBSERVED_FIELDS_ONLY; MISSING_TIMESTAMPS_REMAIN_EXPLICIT",
        "trace_metadata_is_non_authoritative": True,
        "trace_metadata_is_decision_gate": False,
        "business_semantics_changed": False,
        "unobservable_product_phases": [
            "product_message_received_at",
            "screenshot_decode_started_at",
            "final_answer_delivered_at",
        ],
        "refresh_mode": market_quote.get("refresh_mode") or "",
    }

def build_market_domain_projection(current: dict, overseas: dict, us_extended: dict, freshness: dict | None = None) -> dict:
    """Expose domain facts without flattening them into A-share CURRENT semantics."""
    a_freshness = current.get("data_freshness") or {}
    query_freshness = freshness or {}
    domains = {
        "A_SHARE": {
            "market_date": current.get("market_date") or a_freshness.get("market_date") or "",
            "market_phase": current.get("market_phase") or a_freshness.get("market_phase") or "",
            "provider": a_freshness.get("provider") or "",
            "provider_as_of_beijing": a_freshness.get("provider_as_of") or "",
            "as_of_beijing": current.get("captured_at") or a_freshness.get("captured_at_beijing") or "",
            "quality_status": a_freshness.get("status") or "MISSING",
            "freshness_status": query_freshness.get("status") or "STALE",
            "latest_snapshot": current.get("latest_snapshot") or "",
            "objects": [],
        },
        "APAC": {
            "market_date": "",
            "market_phase": "MIXED_BY_OBJECT",
            "provider": "DOMAIN_OBJECT_PROVIDERS",
            "provider_as_of_beijing": "",
            "as_of_beijing": overseas.get("generated_at_beijing") or "",
            "quality_status": overseas.get("quality_status") or "MISSING",
            "freshness_status": "MIXED_BY_OBJECT",
            "objects": [],
        },
        "US_CASH_REFERENCE": {
            "market_date": "",
            "market_phase": "MIXED_BY_OBJECT",
            "provider": "DOMAIN_OBJECT_PROVIDERS",
            "provider_as_of_beijing": "",
            "as_of_beijing": overseas.get("generated_at_beijing") or "",
            "quality_status": overseas.get("quality_status") or "MISSING",
            "freshness_status": "MIXED_BY_OBJECT",
            "objects": [],
        },
        "US_EXTENDED_HOURS": {
            "market_date": "",
            "market_phase": "MIXED_BY_OBJECT",
            "provider": "DOMAIN_OBJECT_PROVIDERS",
            "provider_as_of_beijing": "",
            "as_of_beijing": us_extended.get("generated_at_beijing") or "",
            "quality_status": us_extended.get("quality_status") or "MISSING",
            "freshness_status": "MIXED_BY_OBJECT",
            "objects": [],
        },
    }
    apac_objects = {"N225", "KOSPI", "TWII", "HSTECH"}
    us_cash_objects = {"NDX", "SOX"}
    for key, value in (overseas.get("objects") or {}).items():
        if not isinstance(value, dict):
            continue
        latest = value.get("latest") or {}
        domain = "APAC" if key in apac_objects else "US_CASH_REFERENCE" if key in us_cash_objects else None
        if domain is None:
            continue
        domains[domain]["objects"].append({
            "object": key,
            "market_date": latest.get("market_date_local") or "",
            "market_phase": value.get("market_phase_at_generation") or "",
            "provider": value.get("provider") or "",
            "provider_as_of_beijing": latest.get("as_of_beijing") or "",
            "as_of_beijing": latest.get("as_of_beijing") or value.get("as_of_beijing") or "",
            "quality_status": value.get("quality_status") or "MISSING",
            "freshness_status": value.get("freshness_status") or "MISSING",
        })
    us_objects = us_extended.get("objects") or us_extended.get("proxies") or {}
    if isinstance(us_objects, list):
        us_objects = {str(item.get("object") or item.get("symbol") or item.get("code")): item for item in us_objects if isinstance(item, dict)}
    for key, value in us_objects.items():
        if not isinstance(value, dict):
            continue
        latest = value.get("latest") or {}
        domains["US_EXTENDED_HOURS"]["objects"].append({
            "object": key,
            "market_date": latest.get("market_date_local") or value.get("market_date") or "",
            "market_phase": value.get("market_phase_of_latest") or value.get("market_phase") or "",
            "provider": value.get("provider") or "",
            "provider_as_of_beijing": latest.get("as_of_beijing") or value.get("data_time_beijing") or "",
            "as_of_beijing": latest.get("as_of_beijing") or value.get("data_time_beijing") or "",
            "quality_status": value.get("quality_status") or "MISSING",
            "freshness_status": value.get("freshness_status") or value.get("freshness_status_of_latest") or "MISSING",
        })
    return {
        "domains": domains,
        "identity_rule": "每个市场域和对象保留自己的market_date、market_phase、provider时点、quality与freshness；不得把A股CURRENT的日期扁平化为全球日期。",
        "consumption_rule": "这是由既有域事实重建的派生消费投影；fresh domain pulse减少不必要补采，但不放宽显式latest/current请求的新鲜度门槛。",
        "read_only": True,
    }
def build(root: Path = ROOT, *, force_refresh: bool = False, requested_symbols: list[str] | None = None, request_file: str | None = None, run_discovery: bool = False) -> dict:
    # Only read request identity/time before freshness assurance. All decision
    # facts and derived context are deliberately loaded after the router returns.
    request_payload = {}
    request_time = None
    if request_file:
        request_path = Path(request_file)
        request_payload = read_json((root / request_path).resolve(), {})
        request_payload["_request_file"] = request_path.as_posix()
        request_time = parse_time(request_payload.get("requested_at_beijing") or request_payload.get("request_time"))
    live_dir = root / "requests" / "live_snapshot"
    if request_time is None and live_dir.exists():
        request_times = []
        for path in live_dir.glob("*.json"):
            request = read_json(path, {})
            stamp = parse_time(request.get("requested_at_beijing") or request.get("request_time"))
            if stamp:
                request_times.append(stamp)
        if request_times:
            request_time = max(request_times)
    # Freshness assurance is the first decision-critical operation.
    market_quote = build_market_quote_context(root, force_refresh=force_refresh, requested_symbols=requested_symbols, decision_request_time=request_time)
    # Re-read canonical facts so formal reasoning consumes the post-refresh snapshot.
    current = read_current(root)
    account = read_account_fact(root)
    policy = read_json(root / CANONICAL_FILES["runtime_policy"], {})
    runtime_health = read_json(root / CANONICAL_FILES["runtime_health"], {})
    overseas_context = read_json(root / CANONICAL_FILES["overseas_context"], {})
    us_extended = read_json(root / CANONICAL_FILES["us_extended_hours_context"], {})
    stock_context = read_json(root / CANONICAL_FILES["stock_context"], {})
    stock_market_context = read_json(root / CANONICAL_FILES["stock_market_context"], {})
    consistency = read_json(root / CANONICAL_FILES["system_consistency"], {})
    etf_universe = read_json(root / CANONICAL_FILES["etf_monitor_universe"], {})
    trading_calendar = read_json(root / CANONICAL_FILES["trading_calendar"], {})
    freshness = evaluate_freshness(current, policy)
    market_domain_projection = build_market_domain_projection(current, overseas_context, us_extended, freshness)
    trading_day_status = current_trading_day_status(trading_calendar)
    account_gate = account_gate_status(current, account, policy)
    managed_etf_codes = {str(x.get("code") or "") for x in (etf_universe.get("objects") or []) if x.get("code")}
    managed_etf_codes.update(
        str(x.get("code") or x.get("symbol") or x.get("security_code") or "")
        for x in (account.get("positions") or [])
        if x.get("code") or x.get("symbol") or x.get("security_code")
    )
    held_etf_codes = {str(x) for x in active_account_asset_codes(root, account).get("etf", set())}
    formal_discovery = {"status": "NOT_REQUESTED", "candidates": []}
    discovery_pipeline_started = time.monotonic() if (force_refresh or request_file) else None
    candidate_quote_elapsed = 0.0
    if force_refresh or request_file or run_discovery:
        formal_discovery = discover_formal_candidates(
            root,
            market_date=str(current.get("market_date") or trading_day_status.get("market_date") or ""),
            managed_codes=managed_etf_codes,
            held_codes=held_etf_codes,
        )
        discovered_codes = [str(x.get("code") or "") for x in (formal_discovery.get("candidates") or []) if x.get("code")]
        existing_quote_symbols = {
            str(x.get("symbol") or x.get("code") or "").upper().replace(".SH", "").replace(".SZ", "")
            for x in (market_quote.get("quotes") or []) if isinstance(x, dict)
        }
        quote_refresh_codes = [x for x in discovered_codes if x.upper() not in existing_quote_symbols]
        if discovered_codes and not quote_refresh_codes:
            formal_discovery = attach_formal_quotes(formal_discovery, market_quote)
        elif discovered_codes:
            candidate_quote_started = time.monotonic()
            candidate_quote = build_market_quote_context(
                root,
                force_refresh=True,
                requested_symbols=quote_refresh_codes,
                decision_request_time=None,
            )
            candidate_quote_elapsed = round(time.monotonic() - candidate_quote_started, 3)
            formal_discovery = attach_formal_quotes(formal_discovery, candidate_quote)
            discovered_set = {x.upper() for x in discovered_codes}
            for quote in candidate_quote.get("quotes") or []:
                symbol = str(quote.get("symbol") or quote.get("code") or "").upper().replace(".SH", "").replace(".SZ", "")
                if symbol in discovered_set and symbol not in existing_quote_symbols:
                    market_quote.setdefault("quotes", []).append(quote)
                    existing_quote_symbols.add(symbol)
            market_quote.setdefault("refresh_failures", []).extend(
                x for x in (candidate_quote.get("refresh_failures") or [])
                if str(x.get("symbol") or "").upper().replace(".SH", "").replace(".SZ", "") in discovered_set
            )
        discovery_latency = formal_discovery.setdefault("latency_observability", {})
        discovery_latency["candidate_formal_quote_elapsed_seconds"] = candidate_quote_elapsed
        discovery_latency["discovery_pipeline_elapsed_seconds"] = round(time.monotonic() - discovery_pipeline_started, 3) if discovery_pipeline_started is not None else None
        discovery_latency["measurement_role"] = "OBSERVABILITY_ONLY_NOT_DECISION_GATE"
    system_objects = []
    seen_system_codes = set()
    for item in etf_universe.get("objects") or []:
        code = str(item.get("code") or "").upper()
        if code and code not in seen_system_codes:
            system_objects.append({"object_code": code, "object_name": item.get("name") or code, "source_type": "SYSTEM_MONITORED"})
            seen_system_codes.add(code)
    for code in ("000001.SH", "399006.SZ", "NDX", "SOX", "N225", "KOSPI", "TWII", "HSTECH"):
        if code not in seen_system_codes:
            system_objects.append({"object_code": code, "source_type": "SYSTEM_MONITORED"})
            seen_system_codes.add(code)
    for position in account.get("positions") or []:
        code = str(position.get("code") or "").upper()
        if int(position.get("quantity") or 0) > 0 and code not in seen_system_codes:
            system_objects.append({"object_code": code, "object_name": position.get("name") or code, "source_type": "SYSTEM_MONITORED"})
            seen_system_codes.add(code)
    for item in formal_discovery.get("candidates") or []:
        code = str(item.get("code") or "").upper()
        if code and code not in seen_system_codes:
            system_objects.append({"object_code": code, "object_name": item.get("name") or code, "source_type": "NODE_LOCAL_OBSERVATION_EVALUATION"})
            seen_system_codes.add(code)
    decision = build_decision_context(root, formal_discovery=formal_discovery)
    generated_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    fact_pack = build_decision_fact_pack(root, request_payload, current, account, decision, market_quote, formal_discovery=formal_discovery)
    latency = build_fast_path_latency(request_payload, current, account, decision, market_quote, generated_at, root)
    return {
        "generated_at": now_utc(), "generated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "market_date": current.get("market_date", ""), "latest_valid_node": current.get("latest_valid_node", ""),
        "rules_version": decision.get("rules_version", ""), "current": current, "decision_context": decision,
        "lifecycle_projection": decision.get("lifecycle_projection", {}),
        "analysis_coverage": decision.get("analysis_coverage", {}),
        "etf_strategy_risk_metrics": decision.get("etf_strategy_risk_metrics", {}),
        "data_quality_summary": decision.get("data_quality_summary", {}),
        "point_in_time": decision.get("point_in_time", {}),
        "scheduled_pulse_health": decision.get("scheduled_pulse_health", {}),
        "formal_action": decision.get("formal_action", {}),
        "decision_read_plan": build_read_plan(current, account, policy, freshness),
        "market_quote_router": market_quote,
        "system_objects": system_objects, "user_requested_objects": [], "market_domain_projection": market_domain_projection,
        "formal_etf_discovery": formal_discovery,
        "canonical_files": CANONICAL_FILES, "data_status": {**(current.get("data_freshness") or {}), **freshness}, "freshness_at_context_build": freshness,
        "interactive_decision_freshness": market_quote.get("decision_freshness", {}),
        "trading_day_status": trading_day_status, "runtime_health": runtime_health,
        "system_consistency_status": consistency.get("status", "MISSING"), "system_consistency_hard_errors": consistency.get("hard_error_count", None),
        "etf_universe_count": len(etf_universe.get("objects") or []), "formal_discovery_candidate_count": len(formal_discovery.get("candidates") or []), "overseas_context_status": overseas_context.get("quality_status", "MISSING"),
        "overseas_generated_at_beijing": overseas_context.get("generated_at_beijing", ""),
        "us_extended_hours_status": us_extended.get("quality_status", "MISSING"), "us_extended_hours_generated_at_beijing": us_extended.get("generated_at_beijing", ""),
        "stock_context_status": stock_context.get("account_fact_status", "MISSING"), "stock_market_context_status": stock_market_context.get("quality_status", "MISSING"),
        "stock_role_confirmation_needed": stock_context.get("needs_role_confirmation", False), "account_fact_status": account["status"], "account_gate": account_gate,
        "needs_account_screenshot": not account_gate["can_use_current_account_fact"], "read_only": True,
        "interaction_boundary": "用户主动查询时先进入freshness assurance，再按‘查询时立即补采/复用 → 最近一次有效快照 → 明确降级/缺失’获取行情；正式输出必须标注北京时间真实数据时点，并区分美股现金盘、盘后和盘前。",
        "decision_fact_pack": fact_pack,
        "formal_reply_freeze": fact_pack.get("formal_reply_freeze", {}),
        "freshness_assurance": {"status": (market_quote.get("decision_freshness") or {}).get("status", "UNKNOWN"), "refresh_mode": market_quote.get("refresh_mode", ""), "decision_request_time": request_time.isoformat() if request_time else "", "decision_request_post_current": (market_quote.get("decision_freshness") or {}).get("post_request", False), "rule": "freshness assurance precedes full decision-context construction; post-refresh CURRENT is re-read before formal decision context construction."},
        "fast_path_latency": latency,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--request-file", default="", help="explicit live_snapshot request payload for this analysis")
    parser.add_argument("--run-discovery", action="store_true", help="run bounded all-market Discovery from an already-current production snapshot without forcing a second core refresh")
    args = parser.parse_args()
    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]
    context = build(ROOT, force_refresh=args.force_refresh, requested_symbols=symbols, request_file=args.request_file or None, run_discovery=args.run_discovery)
    atomic_json_write(ROOT / "data" / "state" / "query_context.json", context)
    print(json.dumps({"ok": True, "generated_at_beijing": context["generated_at_beijing"], "market_date": context["market_date"], "latest_valid_node": context["latest_valid_node"], "system_consistency_status": context["system_consistency_status"], "candidate_trading_day": context["trading_day_status"]["is_candidate_trading_day"], "etf_universe_count": context["etf_universe_count"], "account_fact_status": context["account_fact_status"], "account_usable": context["account_gate"]["can_use_current_account_fact"], "freshness": context["freshness_at_context_build"]["status"], "overseas_context_status": context["overseas_context_status"], "us_extended_hours_status": context["us_extended_hours_status"], "stock_context_status": context["stock_context_status"], "stock_market_context_status": context["stock_market_context_status"], "read_plan_mode": context["decision_read_plan"]["mode"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

