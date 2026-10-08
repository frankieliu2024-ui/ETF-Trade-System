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
    from trading_time_semantics import user_visible_trading_time_anchor
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
    from scripts.build_stock_context import active_account_asset_codes
    from scripts.market_quote_router import build_market_quote_context
    from scripts.formal_etf_opportunity_discovery import attach_formal_quotes, discover_formal_candidates
    from scripts.trading_time_semantics import user_visible_trading_time_anchor

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


def _discovery_universe_identity(universe: dict) -> str:
    """Stable identity for the existing monitored ETF universe, not a TTL."""
    codes = sorted(
        str(item.get("code") or item.get("symbol") or "").upper()
        for item in (universe.get("objects") or [])
        if isinstance(item, dict) and (item.get("code") or item.get("symbol"))
    )
    payload = {"version": str(universe.get("version") or ""), "codes": codes}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _quote_symbol(quote: dict) -> str:
    return str(quote.get("symbol") or quote.get("code") or "").upper().replace(".SH", "").replace(".SZ", "")


def _merge_discovery_candidate_quotes(discovered_codes: list[str], existing_quote: dict, refreshed_quote: dict) -> dict:
    """Union candidate quotes without turning mixed coverage into false UNAVAILABLE."""
    discovered_set = {str(code).upper() for code in discovered_codes}
    by_symbol = {}
    for quote in existing_quote.get("quotes") or []:
        if isinstance(quote, dict):
            symbol = _quote_symbol(quote)
            if symbol in discovered_set:
                by_symbol[symbol] = quote
    # The refresh is explicitly requested for missing candidates, so a returned
    # quote for the same symbol is the newer request-local evidence.
    for quote in refreshed_quote.get("quotes") or []:
        if isinstance(quote, dict):
            symbol = _quote_symbol(quote)
            if symbol in discovered_set:
                by_symbol[symbol] = quote
    return {"quotes": [by_symbol[str(code).upper()] for code in discovered_codes if str(code).upper() in by_symbol]}


def _discovery_delta_identity(root: Path) -> str:
    """Identify canonical market-delta semantics, not a snapshot filename or TTL."""
    delta = read_json(root / CANONICAL_FILES["market_delta"], {})
    semantic = {"market_date": delta.get("market_date"), "mode": delta.get("mode"), "status": delta.get("status"), "changes": delta.get("changes") or []}
    return hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _reusable_formal_discovery(root: Path, current: dict, universe_identity: str, managed_codes: set[str]) -> dict | None:
    """Reuse a terminal result only when it belongs to the same legal market node."""
    prior = read_json(root / "data" / "state" / "query_context.json", {})
    discovery = prior.get("formal_etf_discovery") or {}
    if str(discovery.get("status") or "").upper() != "READY":
        return None
    prior_current = prior.get("current") or {}
    if str(prior.get("market_date") or prior_current.get("market_date") or "") != str(current.get("market_date") or ""):
        return None
    if str(prior.get("latest_valid_node") or prior_current.get("latest_valid_node") or "") != str(current.get("latest_valid_node") or ""):
        return None
    if str(discovery.get("universe_identity") or "") != universe_identity:
        return None
    prior_delta_identity = str(discovery.get("discovery_delta_identity") or "")
    if not prior_delta_identity or prior_delta_identity != _discovery_delta_identity(root):
        return None
    reused = dict(discovery)
    candidates = []
    for item in discovery.get("candidates") or []:
        candidate = dict(item)
        code = str(candidate.get("code") or "")
        candidate["management_identity"] = "MANAGED" if code in managed_codes else None
        candidate["discovery_semantic"] = (
            "NODE_LOCAL_ALL_MARKET_OPPORTUNITY_SIGNAL_FOR_EXISTING_MANAGED_ETF"
            if code in managed_codes else "NODE_LOCAL_OBSERVATION_EVALUATION_INPUT"
        )
        candidates.append(candidate)
    reused["candidates"] = candidates
    reused["managed_identity_count"] = sum(1 for item in candidates if item.get("management_identity") == "MANAGED")
    reused["reuse"] = {
        "mode": "SAME_MARKET_NODE_LEGAL_DISCOVERY_EVIDENCE_REUSE",
        "source": "data/state/query_context.json",
        "qualification": "same_market_date_same_latest_valid_node_same_discovery_delta_identity_same_universe_identity",
        "fixed_ttl": False,
    }
    return reused


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


def _latest_observation_thesis_state(root: Path, observation_codes: set[str]) -> dict[str, dict]:
    """Read the latest durable Formal Decision thesis metadata for current observations."""
    out: dict[str, dict] = {}
    decisions_dir = root / "events" / "decisions"
    if not decisions_dir.exists() or not observation_codes:
        return out
    paths = sorted(decisions_dir.glob("*.json"), reverse=True)
    required = ("name", "thscode", "thesis", "falsifier", "next_decision_information", "information_value_reason")
    for path in paths:
        if len(out) >= len(observation_codes):
            break
        try:
            event = read_json(path, {})
        except Exception:
            continue
        decision = event.get("formal_decision") or {}
        for item in decision.get("observation_management") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").replace(".SH", "").replace(".SZ", "").strip()
            if code not in observation_codes or code in out:
                continue
            if str(item.get("action") or "").upper() not in {"ADMIT", "RETAIN"}:
                continue
            if all(str(item.get(key) or "").strip() for key in required):
                out[code] = {key: item.get(key) for key in required}
    return out


def _latest_formal_decision_baseline(root: Path, request_time: str = "") -> dict:
    """Return the latest canonical Formal Decision strictly before this request.

    The baseline is request input only: it does not authorize a transition and
    does not create a second state store.  It lets the business Actor explain
    why a material state is changing instead of silently regenerating it.
    """
    decisions_dir = root / "events" / "decisions"
    if not decisions_dir.exists():
        return {}
    cutoff = parse_time(request_time) if request_time else None
    candidates = []
    for path in decisions_dir.glob("*.json"):
        try:
            event = read_json(path, {})
        except Exception:
            continue
        decision = event.get("formal_decision") or {}
        when_raw = str(event.get("decision_time_beijing") or decision.get("data_as_of_beijing") or "")
        when = parse_time(when_raw)
        if cutoff is not None and when is not None and when >= cutoff:
            continue
        if not decision:
            continue
        candidates.append((when or parse_time("1970-01-01T00:00:00+00:00"), event, decision))
    if not candidates:
        return {}
    _, event, decision = max(candidates, key=lambda item: item[0])
    holding_actions = {}
    for review in decision.get("managed_position_reviews") or []:
        if not isinstance(review, dict):
            continue
        code = str(review.get("security_code") or review.get("code") or "").strip()
        action = str(review.get("current_action") or review.get("action") or "").strip()
        if code and action:
            holding_actions[code] = action
    return {
        "decision_id": str(event.get("decision_id") or decision.get("decision_id") or ""),
        "decision_time_beijing": str(event.get("decision_time_beijing") or decision.get("data_as_of_beijing") or ""),
        "risk_permission": str(decision.get("risk_permission") or ""),
        "candidate_code": str(decision.get("candidate_code") or ""),
        "candidate_name": str(decision.get("candidate_name") or ""),
        "opportunity_status": str(event.get("_opportunity_status") or decision.get("opportunity_status") or ""),
        "holding_actions": holding_actions,
    }



def _latest_base_stock_replacement_candidates(root: Path, positions: list[dict], request_time: str = "") -> list[dict]:
    """Project the latest PIT-eligible base-stock screen as bounded research candidates.

    This is research supply only. It does not create a stock watchlist, rank an
    unscreened market, grant trade authority, or turn the screen score into an
    action rule. Candidate buy judgment remains separate from any held-stock
    sell judgment in the existing Formal Decision.
    """
    roles = read_json(root / CANONICAL_FILES["asset_roles"], {})
    role_map = roles.get("roles") or roles.get("assets") or {}
    held_codes = {str(item.get("code") or "") for item in positions}
    base_markets = set()
    for code in held_codes:
        role = role_map.get(code) if isinstance(role_map, dict) else None
        role_name = str((role or {}).get("role") if isinstance(role, dict) else "")
        status = str((role or {}).get("status") if isinstance(role, dict) else "")
        if role_name == "IPO_BASE_STOCK" and status == "CONFIRMED":
            base_markets.add("SH" if code.startswith(("60", "68")) else "SZ" if code.startswith(("00", "30")) else "")
    base_markets.discard("")
    if not base_markets:
        return []

    cutoff = parse_time(request_time) if request_time else None
    screen_dir = root / "research" / "base_stock_screen"
    eligible = []
    for path in screen_dir.glob("*.json") if screen_dir.exists() else []:
        obj = read_json(path, {})
        processed = parse_time(str(obj.get("processed_at") or ""))
        if cutoff is not None and processed is not None and processed > cutoff:
            continue
        if not obj.get("ok") or not isinstance(obj.get("records"), list):
            continue
        eligible.append((processed or parse_time("1970-01-01T00:00:00+00:00"), path, obj))
    if not eligible:
        return []
    _, source_path, screen = max(eligible, key=lambda item: item[0])

    out = []
    for rec in screen.get("records") or []:
        if not isinstance(rec, dict):
            continue
        code = str(rec.get("code") or "")
        market = str(rec.get("market") or "")
        is_a_share_stock = (market == "SH" and code.startswith(("60", "68"))) or (market == "SZ" and code.startswith(("00", "30")))
        if not code or code in held_codes or market not in base_markets or not is_a_share_stock:
            continue
        out.append({
            "code": code,
            "name": rec.get("name") or code,
            "market": market,
            "research_role": rec.get("role") or "candidate",
            "screen_metrics": {
                key: rec.get(key) for key in (
                    "return_pct", "ann_vol_pct", "downside_vol_pct",
                    "max_drawdown_pct", "avg_amount", "median_amount",
                )
            },
            "screen_rank": rec.get("market_rank"),
            "screen_score": rec.get("stability_score"),
            "screen_source": str(source_path.relative_to(root)).replace("\\", "/"),
            "screen_processed_at": screen.get("processed_at"),
            "screen_end_date": screen.get("end_date"),
            "authority": "RESEARCH_CANDIDATE_ONLY",
        })
    return out


def _decision_problem_graph(positions: list[dict], discovery_inputs: list[dict], account: dict, observation_inputs: list[dict] | None = None, observation_review_inputs: list[dict] | None = None, base_stock_replacement_inputs: list[dict] | None = None) -> list[dict]:
    problems = [
        {"problem_id": "RISK_PERMISSION", "decision_object": "risk_permission", "required_business_judgment": "风险许可及新增风险边界"},
        {"problem_id": "MAIN_CANDIDATE", "decision_object": "main_candidate", "required_business_judgment": "主候选及机会状态"},
        {"problem_id": "DEPLOYABLE_CASH", "decision_object": "deployable_cash", "required_business_judgment": "现金与其他合法资本用途比较"},
        {"problem_id": "RELEASABLE_CAPITAL", "decision_object": "releasable_capital", "required_business_judgment": "低效率资本是否释放及迁移"},
        {"problem_id": "TRIAL_CONFIRM_CAPACITY", "decision_object": "trial_confirm_capacity", "required_business_judgment": "Trial/Confirm承载能力"},
        {"problem_id": "CONCENTRATION_COMMON_RISK", "decision_object": "concentration_common_risk", "required_business_judgment": "集中度与共同风险"},
        {"problem_id": "NEXT_UNIT_CAPITAL_USE", "decision_object": "next_unit_capital_use", "required_business_judgment": "下一单位资本用途"},
    ]
    for item in positions:
        code = str(item.get("code") or "")
        problems.append({"problem_id": f"HOLDING:{code}", "decision_object": code, "security": item.get("name") or code, "current_quantity": item.get("quantity"), "current_capital_occupation": item.get("market_value"), "alternatives": {"HOLD": "继续占用当前资本", "REDUCE": "部分释放资本", "EXIT": "全部释放资本"}, "required_business_judgment": "比较HOLD/REDUCE/EXIT并确定当前动作、数量、去向和下一变化条件"})
        if str(item.get("asset_type") or "").upper() == "ETF":
            problems.append({"problem_id": f"HELD_ETF_ADD:{code}", "decision_object": code, "security": item.get("name") or code, "required_business_judgment": "判断是否追加资本及相对现金/其他用途的效率"})
    for item in observation_inputs or []:
        code = str(item.get("code") or "")
        if code:
            problems.append({"problem_id": f"OBSERVATION:{code}", "decision_object": code, "security": item.get("name") or code, "thscode": item.get("thscode") or "", "existing_thesis_state": item.get("existing_thesis_state") or {}, "role_contract": "CONTINUOUS_INFORMATION_V1", "required_business_judgment": "先判断持续信息功能是否仍独立、可证伪且不可被更有效对象充分替代，再判断当前机会状态；RETAIN不要求当前接近Trial/Confirm，EXIT必须说明信息功能失效/被替代及退出后的信息连续性成本。观察ETF若当前形成机会可同时作为节点级候选参与资本比较"})
    for item in observation_review_inputs or []:
        code = str(item.get("code") or "")
        if code:
            problems.append({"problem_id": f"OBSERVATION_REVIEW:{code}", "decision_object": code, "security": item.get("name") or code, "thscode": item.get("thscode") or "", "formal_quote_status": item.get("formal_quote_status") or "", "role_contract": "EXPLICIT_OBSERVATION_IDENTITY_REVIEW_V1", "required_business_judgment": "用户显式要求重新评估已退出Observation的对象。先判断是否重新取得独立、可证伪、跨节点持续信息价值；ADMIT/REJECT只决定Observation身份，不自动产生机会、Trial/Confirm或交易动作。"})
    for item in base_stock_replacement_inputs or []:
        code = str(item.get("code") or "")
        if code:
            problems.append({
                "problem_id": f"BASE_STOCK_REPLACEMENT:{code}",
                "decision_object": code,
                "security": item.get("name") or code,
                "market": item.get("market") or "",
                "research_evidence": item,
                "role_contract": "IPO_BASE_REPLACEMENT_CANDIDATE_V1",
                "required_business_judgment": "判断该同市场候选是否独立值得作为打新底仓资本用途；必须与继续持有现底仓、ETF机会和现金比较。筛选排名/分数仅为研究证据，不产生买入许可；允许结论为不替换。",
            })
    for item in discovery_inputs:
        code = str(item.get("code") or "")
        if code:
            problems.append({"problem_id": f"DISCOVERY:{code}", "decision_object": code, "security": item.get("name") or code, "formal_quote_status": item.get("formal_quote_status") or "", "role_contract": "NODE_LOCAL_CANDIDATE_V1", "management_identity": item.get("management_identity"), "required_business_judgment": "判断本节点机会ETF状态及资本竞争位置；机会ETF角色本身不持久。只有另有持续跨节点信息价值时才ADMIT为观察ETF，REJECT观察准入不等于否定本节点候选机会"})
    return problems


def _evidence_requirement_plan(problems: list[dict]) -> list[dict]:
    """Build decision-relevant evidence requirements without copying the full
    three-layer catalogue onto every business problem.

    Three-layer monitoring qualifies shared request-bound facts once.  This
    plan only links each business problem to evidence classes that can change
    that problem's judgment.  It does not weaken monitoring coverage or any
    MASTER/business obligation.
    """
    shared_market = [
        "GLOBAL_RISK", "RATES", "FX", "MACRO_POLICY_EVENTS",
        "A_SHARE_INDEX", "A_SHARE_BREADTH", "A_SHARE_STYLE_FEEDBACK",
        "A_SHARE_INDUSTRY_THEME", "A_SHARE_LIQUIDITY_TURNOVER",
        "A_SHARE_CAPITAL_FLOW", "A_SHARE_ANOMALY", "EXTERNAL_CONFIRMATION_STATE",
    ]
    opportunity = [
        "ETF_RELATIVE_STRENGTH", "FULL_MARKET_DISCOVERY",
        "OBSERVATION_ETF", "TEMPORARY_DISCOVERY_CANDIDATE",
    ]
    holding = ["ETF_RELATIVE_STRENGTH", "HOLDING_ETF", "ACCOUNT_STOCK"]
    capital = ["CASH", "RELEASABLE_CAPITAL", "HOLDING_ADDITIONAL_CAPITAL"]

    plan = []
    for problem in problems:
        pid = problem["problem_id"]
        target = str(problem.get("security") or problem.get("decision_object") or "")
        if pid == "RISK_PERMISSION":
            classes = shared_market
        elif pid == "MAIN_CANDIDATE":
            classes = shared_market + opportunity
        elif pid.startswith("HOLDING:"):
            classes = shared_market + holding
        elif pid.startswith("HELD_ETF_ADD:"):
            classes = shared_market + holding + ["HOLDING_ADDITIONAL_CAPITAL", "CASH"]
        elif pid.startswith(("DISCOVERY:", "OBSERVATION:", "OBSERVATION_REVIEW:")):
            classes = shared_market + opportunity
        elif pid.startswith("BASE_STOCK_REPLACEMENT:"):
            classes = shared_market + ["ACCOUNT_STOCK", "CASH", "RELEASABLE_CAPITAL"]
        elif pid in {"DEPLOYABLE_CASH", "RELEASABLE_CAPITAL", "TRIAL_CONFIRM_CAPACITY",
                     "CONCENTRATION_COMMON_RISK", "NEXT_UNIT_CAPITAL_USE"}:
            classes = shared_market + opportunity + capital
        else:
            # Unknown future problem families fail safe to the shared market
            # packet instead of silently inheriting every object/capital class.
            classes = shared_market

        text = target.lower()
        if any(token in text for token in ("能源", "化工", "油", "资源", "商品")):
            classes += ["COMMODITY", "OVERSEAS_INDUSTRY_CHAIN", "HK_INDUSTRY_CHAIN"]
        elif any(token in text for token in ("半导体", "科技", "芯片", "纳指", "科创")):
            classes += ["OVERSEAS_INDUSTRY_CHAIN", "HK_INDUSTRY_CHAIN"]

        for evidence_class in dict.fromkeys(classes):
            plan.append({
                "requirement_id": f"{pid}:{evidence_class}",
                "target_problem_id": pid,
                "evidence_class": evidence_class,
                "target_exposure": target,
                "required": evidence_class in {"A_SHARE_STYLE_FEEDBACK", "ETF_RELATIVE_STRENGTH"},
                "optional": evidence_class not in {"A_SHARE_STYLE_FEEDBACK", "ETF_RELATIVE_STRENGTH"},
                "why_decision_relevant": "可能改变该问题的风险许可、机会状态、持仓动作或资本配置",
                "satisfaction": "UNSATISFIED",
            })
    return plan

def _decision_work_package(
    graph: list[dict],
    plan: list[dict],
    evidence: list[dict],
    *,
    three_layer_monitoring: dict | None = None,
) -> dict:
    by_problem = {}
    for item in plan:
        by_problem.setdefault(item["target_problem_id"], []).append(item["requirement_id"])

    available = {}
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        cls = str(item.get("evidence_class") or item.get("class") or "").strip()
        if not cls:
            continue
        status = str(item.get("status") or item.get("quality_status") or "SATISFIED").upper()
        available[cls] = status if status in {"SATISFIED", "DEGRADED", "INSUFFICIENT", "NOT_REQUIRED"} else "SATISFIED"

    # Required evidence classes are grounded in the existing qualified
    # three-layer packet.  Generic quote presence is intentionally not enough.
    monitoring = three_layer_monitoring or {}
    layer_2 = monitoring.get("layer_2_a_share_internal") or {}
    layer_3 = monitoring.get("layer_3_etf_opportunity_capital") or {}
    layer_2_domains = layer_2.get("fact_domains") or {}
    regime = layer_2.get("market_regime_context") or {}
    structure = layer_2.get("market_structure_context") or {}
    has_breadth = bool(layer_2_domains.get("breadth") and regime.get("etf_breadth"))
    has_style = bool(layer_2_domains.get("style") and regime.get("style_context"))
    has_structure = bool(layer_2_domains.get("industry_theme") and (structure.get("items") or []))
    if has_breadth and has_style:
        available["A_SHARE_STYLE_FEEDBACK"] = "SATISFIED"
    elif has_breadth or has_style or has_structure:
        available["A_SHARE_STYLE_FEEDBACK"] = "DEGRADED"

    discovery_candidates = layer_3.get("discovery_candidates") or []
    actual_positions = layer_3.get("actual_positions") or []
    observation_inputs = layer_3.get("observation_inputs") or []
    if actual_positions and (observation_inputs or discovery_candidates):
        available["ETF_RELATIVE_STRENGTH"] = "SATISFIED"
    elif actual_positions or observation_inputs or discovery_candidates:
        available["ETF_RELATIVE_STRENGTH"] = "DEGRADED"

    # Optional/triggered classes stay unresolved unless an upstream qualified
    # fact explicitly carries their evidence_class.  Do not infer GLOBAL_RISK,
    # COMMODITY, FX, RATES or industry-chain satisfaction from broad quotes.
    requirement_states = []
    for item in plan:
        status = available.get(item["evidence_class"], "INSUFFICIENT")
        requirement_states.append({**item, "satisfaction": status})
    unresolved = [x["requirement_id"] for x in requirement_states if x["required"] and x["satisfaction"] not in {"SATISFIED", "NOT_REQUIRED"}]
    return {
        "schema_version": "1.0", "business_role_reconciliation_contract": "V1", "problem_graph": graph,
        "evidence_requirements": requirement_states, "current_evidence": evidence,
        "response_contract": {"must_answer": ["final_action", "capital_comparison", "next_change_condition", "evidence_decision_impact"], "holding_additional_fields": ["capital_occupancy_reason", "higher_efficiency_alternative", "quantity_if_reduce_or_exit", "capital_destination_if_reduce_or_exit"], "main_candidate_additional_fields": ["candidate_code", "candidate_name", "opportunity_status"], "opportunity_additional_fields": ["disposition", "reason", "opportunity_status", "for_DISCOVERY_ADMIT_only:name,thscode,thesis,falsifier,next_decision_information,information_value_reason"], "next_unit_additional_fields": ["new_amount_yuan", "post_action_deployable_cash", "future_opportunity_capacity", "cash_opportunity_cost", "alternative_capital_use_review", "concentration_account_structure_effect", "selected_state_reason", "compared_capital_states_as_business_state_names", "zero_amount_decisive_reason_if_zero", "capital_migration_review"], "schema_knowledge_required": False, "rule": "Actor answers only business questions keyed by problem_id. Canonical lifecycle, managed reviews, opportunity reviews, capital_use and evidence-consumption nesting are machine projections."},
        "decision_marginal_stop": {
            "required_states": ["SATISFIED", "DEGRADED", "INSUFFICIENT", "NOT_REQUIRED"],
            "expansion_required": bool(unresolved), "expansion_complete": not unresolved,
            "unresolved_required_requirements": unresolved,
            "stop_rule": "继续扩展仅当未解决required evidence仍可能改变risk permission、candidate、holding action、amount、capital migration或next-unit capital use。",
        },
        "problem_evidence_index": by_problem,
    }


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
            "formal_quote_status": item.get("formal_quote_status") or "",
            "evidence": item.get("evidence") or item.get("features") or {},
        })

    request_id = str(request.get("request_id") or "").strip()
    requested_at = _request_received_at_beijing(request)
    request_source = str(request.get("source") or request.get("requested_by") or "").strip().upper()
    manual_formal_sources = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "CHATGPT_USER_CONTINUE",
        "CHATGPT_USER_GITHUB_INTRADAY",
        "CHATGPT_USER_GITHUB_DECISION",
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
        "rule": "Manual formal replies wait only for same-request action-determinative facts that are still legally forming. Once those facts resolve (including explicit terminal degradation), complete analysis and the first user-visible decision reply must proceed without waiting for Decision Fact persistence/post-write acceptance or any downstream Dashboard/account/E2E/notification/research projection. Persistence failure is disclosed as '正式决策尚未持久化' and repaired separately; it does not suppress an otherwise valid business decision reply.",
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
    coverage = decision.get("analysis_coverage") or {}
    held_etfs_total = int(coverage.get("held_etfs_total") or 0)
    held_etfs_available = int(coverage.get("held_etfs_available") or 0)
    account_stocks_total = int(coverage.get("account_stocks_total") or 0)
    account_stocks_available = int(coverage.get("account_stocks_available") or 0)
    missing_held_etfs = [str(x) for x in (coverage.get("missing_held_etfs") or []) if str(x)]
    missing_account_stocks = [str(x) for x in (coverage.get("missing_account_stocks") or []) if str(x)]
    actual_position_market_coverage_complete = (
        held_etfs_available >= held_etfs_total
        and account_stocks_available >= account_stocks_total
        and not missing_held_etfs
        and not missing_account_stocks
    )
    if account_ready and positions and not actual_position_market_coverage_complete:
        action_blockers.append("ACTUAL_POSITION_MARKET_COVERAGE_INCOMPLETE")
    formal_action_readiness = {
        "status": "READY" if not action_blockers else "NOT_READY",
        "ready": not action_blockers,
        "blockers": action_blockers,
        "request_bound": bool(request_id and requested_at),
        "request_scoped_pit_resolved": post_request,
        "fallback_available": fallback_available,
        "account_fact_ready": account_ready,
        "actual_position_market_coverage_complete": actual_position_market_coverage_complete,
        "missing_held_etfs": missing_held_etfs,
        "missing_account_stocks": missing_account_stocks,
        "rule": "Exact amount/share/action canonical completion remains fail-closed on request identity, action-qualified request-scoped PIT, valid account truth, and complete market-evidence coverage for every actual positive-quantity position. This gate must not be interpreted as a ban on independent legal analysis.",
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

    held_etf_codes = {str(item.get("code") or "") for item in positions if str(item.get("asset_type") or "").upper() == "ETF"}
    raw_observations = [
        item for item in (universe.get("objects") or [])
        if str(item.get("code") or "") and str(item.get("code") or "") not in held_etf_codes
    ]
    observation_codes = {str(item.get("code") or "") for item in raw_observations}
    observation_thesis = _latest_observation_thesis_state(root, observation_codes)
    observation_inputs = []
    for item in raw_observations:
        code = str(item.get("code") or "")
        observation_inputs.append({
            "code": code,
            "name": item.get("name") or code,
            "thscode": item.get("thscode") or "",
            "existing_thesis_state": observation_thesis.get(code) or {},
        })
    observation_review_inputs = []
    existing_role_codes = held_etf_codes | observation_codes
    quote_by_code = {
        str(item.get("symbol") or item.get("code") or "").upper().replace(".SH", "").replace(".SZ", ""): item
        for item in (market_quote.get("quotes") or []) if isinstance(item, dict)
    }
    seen_review_codes = set()
    for item in request.get("observation_identity_review_targets") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").upper().replace(".SH", "").replace(".SZ", "").strip()
        if len(code) != 6 or not code.isdigit() or code in seen_review_codes or code in existing_role_codes:
            continue
        quote = quote_by_code.get(code) or {}
        quote_status = "READY" if quote.get("latest_price") is not None or quote.get("close") is not None else "FAILED"
        observation_review_inputs.append({
            "code": code,
            "name": item.get("name") or code,
            "thscode": item.get("thscode") or "",
            "formal_quote_status": quote_status,
            "request_bound_explicit_review": True,
        })
        seen_review_codes.add(code)
    base_stock_replacement_inputs = _latest_base_stock_replacement_candidates(
        root, positions, str(request.get("requested_at_beijing") or request.get("request_time") or "")
    )
    problem_graph = _decision_problem_graph(
        positions, discovery_inputs, account, observation_inputs, observation_review_inputs,
        base_stock_replacement_inputs,
    )
    evidence_plan = _evidence_requirement_plan(problem_graph)

    # Project already-produced monitoring facts into the request-bound packet.
    # This is a read-only projection: it does not create facts, rank candidates,
    # or grant trade authority.
    market_regime_context = read_json(root / "data" / "state" / "market_regime_context.json", {})
    market_structure_context = read_json(root / "data" / "state" / "market_structure_context.json", {})
    overseas_context = read_json(root / CANONICAL_FILES["overseas_context"], {})
    us_extended_hours_context = read_json(root / CANONICAL_FILES["us_extended_hours_context"], {})

    # Fixed source-binding contract: each monitoring layer must expose the
    # canonical source bundle it consumed. This is a projection contract only;
    # it does not add providers or grant action authority.
    three_layer_source_contract = {
        "layer_1_external_cross_market": {
            "required_sources": [
                {"source_id": "OVERSEAS_CONTEXT", "path": CANONICAL_FILES["overseas_context"], "provider_field": "provider"},
                {"source_id": "US_EXTENDED_HOURS", "path": CANONICAL_FILES["us_extended_hours_context"], "provider_field": "provider"},
            ],
            "required_domains": ["global_risk", "rates", "fx", "overseas_industry_theme", "macro_policy_supply_chain"],
            "degrade_rule": "If a required domain is unavailable or stale, mark the domain DEGRADED; never substitute an overseas equity index list.",
        },
        "layer_2_a_share_internal": {
            "required_sources": [
                {"source_id": "A_SHARE_CURRENT", "path": CANONICAL_FILES["current"], "provider_field": "provider"},
                {"source_id": "A_SHARE_MARKET_STRUCTURE", "path": "data/state/market_structure_context.json", "provider_field": "provider"},
                {"source_id": "A_SHARE_MARKET_REGIME", "path": "data/state/market_regime_context.json", "provider_field": "provider"},
            ],
            "required_domains": ["index", "breadth", "style", "industry_theme", "liquidity_turnover", "capital_flow", "anomaly", "external_confirmation"],
            "degrade_rule": "Index-only output is insufficient; missing internal domains remain explicit DEGRADED.",
        },
        "layer_3_etf_opportunity_capital": {
            "required_sources": [
                {"source_id": "BROKER_ACCOUNT_FACT", "path": CANONICAL_FILES["account_fact"], "provider_field": "source"},
                {"source_id": "ETF_DISCOVERY_UNIVERSE", "path": CANONICAL_FILES["etf_monitor_universe"], "provider_field": "source"},
                {"source_id": "STOCK_CONTEXT", "path": CANONICAL_FILES["stock_context"], "provider_field": "source"},
            ],
            "required_domains": ["holdings", "cash", "releasable_capital", "discovery", "observation", "account_stock", "conditional_industry_chain"],
            "degrade_rule": "No account/industry fact may be invented; absence is explicit and localized.",
        },
    }
    three_layer_monitoring = {
        "source_contract": three_layer_source_contract,
        "layer_1_external_cross_market": {
            "base_evidence": {
                "overseas_context": overseas_context,
                "us_extended_hours_context": us_extended_hours_context,
            },
            "triggered_expansion": {
                "status": "CONDITIONAL",
                "rule": "Expand only when current exposure, candidate, anomaly, commodity/macro event, or transmission hypothesis can change risk, opportunity, sell/hold, or capital allocation.",
            },
            "decision_boundary": "External facts are evidence only; asynchronous overseas facts must not be described as synchronous A-share confirmation.",
        },
        "layer_2_a_share_internal": {
            "market_regime_context": market_regime_context,
            "market_structure_context": market_structure_context,
            "fact_domains": {
                "index": bool(market_regime_context.get("indices")),
                "breadth": bool(market_regime_context.get("etf_breadth")),
                "style": bool(market_regime_context.get("style_context")),
                "industry_theme": bool((market_structure_context.get("items") or [])),
                "liquidity_turnover": bool((market_structure_context.get("items") or [])),
                "capital_flow": "OPTIONAL_OR_TRIGGERED",
                "anomaly": "OPTIONAL_OR_TRIGGERED",
                "external_confirmation_state": "ACTOR_DERIVED_FROM_QUALIFIED_LAYER_1_AND_LAYER_2_FACTS",
            },
            "decision_boundary": "A-share internal monitoring is not satisfied by fixed indices alone when qualified breadth/style/structure evidence already exists.",
        },
        "layer_3_etf_opportunity_capital": {
            "formal_discovery_status": discovery.get("status") or "NOT_REQUESTED",
            "discovery_candidates": discovery_inputs,
            "base_stock_replacement_candidates": base_stock_replacement_inputs,
            "actual_positions": positions,
            "observation_inputs": observation_inputs,
            "observation_identity_review_inputs": observation_review_inputs,
            "deployable_cash": account.get("deployable_cash"),
            "required_capital_states": [
                "all_actual_positions_continue_or_release",
                "held_etf_additional_capital",
                "all_observation_etfs",
                "temporary_discovery_candidates",
                "cash",
                "releasable_low_efficiency_capital",
            ],
            "decision_boundary": "Inputs only; MASTER and ChatGPT own sell/hold, candidate selection, amount, funding source, and next-unit capital use.",
        },
        "completeness_rule": "Three-layer monitoring means qualified evidence across the current request's relevant fact domains, not a fixed-index checklist. Missing optional/triggered evidence remains explicit and localized.",
    }

    work_package = _decision_work_package(
        problem_graph,
        evidence_plan,
        quotes,
        three_layer_monitoring=three_layer_monitoring,
    )
    work_package["three_layer_monitoring_evidence"] = three_layer_monitoring
    work_package["previous_formal_decision_baseline"] = _latest_formal_decision_baseline(root, _request_received_at_beijing(request))
    work_package["response_contract"]["material_state_transition_attribution"] = {"required_when_changed": ["risk_permission", "main_candidate_or_opportunity_status", "holding_action"], "fields": ["material_evidence_delta", "evidence_requirement_ids"], "rule": "Material state changes versus the previous canonical Formal Decision require explicit attribution to qualified current-request evidence. Execution/market-phase or cash constraints must not mechanically rewrite orthogonal risk/opportunity states."}

    return {
        "schema_version": "1.5",
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
        "decision_work_package": work_package,
        "three_layer_monitoring_evidence": three_layer_monitoring,
        "decision_problem_graph": problem_graph,
        "evidence_requirement_plan": evidence_plan,
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
            "EXPLICIT_DECISION_EVIDENCE_CONSUMPTION_BOUND_TO_THIS_REQUEST",
        ],
        "conditional_reads": {
            "experience": CANONICAL_FILES["experience"],
            "market_archive": CANONICAL_FILES["market_archive"],
            "rule": "Read only when the evidence can change risk permission, opportunity, lifecycle, amount, funding source, sell action, account truth, or capital allocation.",
        },
        "deferred_non_blocking": [
            "CANONICAL_DECISION_PERSISTENCE_AFTER_BUSINESS_DECISION_IS_FORMED",
            "CANONICAL_DECISION_POST_WRITE_ACCEPTANCE_AFTER_BUSINESS_DECISION_IS_FORMED",
            "DASHBOARD_RENDER",
            "ACCOUNT_DECISION_PROJECTION",
            "MARKET_ARCHIVE_RENDER",
            "REVIEW_CONTEXT",
            "NOTIFICATION_RENDER",
            "RESEARCH_OR_EXPERIENCE_PROJECTION",
            "NON_DECISION_E2E_PROJECTION",
        ],
        "user_visible_reply_boundary": {
            "rule": "The first user-visible formal reply is the complete business decision analysis. It must not be replaced by an interim waiting/persistence/acceptance/projection status once action-determinative facts have resolved.",
            "persistence_failure_disclosure": "正式决策尚未持久化。",
            "non_blocking_after_business_decision_formed": True,
            "business_language_contract": {
                "normal_business_mode": "User-visible prose states result, evidence, impact, and next step in plain business language. Internal machine schemas and enums remain unchanged but are not copied into ordinary replies merely to prove execution.",
                "translate_not_suppress": "Preserve decision-relevant timing, freshness, source quality, degradation, lifecycle, observation, and completion meaning; translate control-plane labels into their business effect instead of deleting material facts.",
                "examples": {
                    "request_bound_ready": "本次判断所需数据已满足决策要求，目前无影响判断的关键缺口。",
                    "held_etf_no_add": "现有ETF持仓本次均不建议追加资金。",
                    "persistence_pending": "本次判断已形成，但相关正式状态更新尚未全部确认。",
                    "observation_exit": "本次判断认为应移出观察范围。",
                    "freshness": "行情截至相应时点，并说明是否满足本次判断的时效要求。",
                },
                "technical_audit_exception": "Explicit technical-audit or fault-diagnosis mode may expose internal terms when they are the subject of the audit.",
                "rule": "Do not expose request-bound/READY/blocker/NO_ADD/BUSINESS_DECISION_READY/reply_freezable/canonical persistence/Observation ADMIT-RETAIN-EXIT or equivalent control-plane labels in ordinary business prose when the same fact has an accurate business-language expression.",
            },
            "execution_status_contract": {
                "before_business_decision": "已自动接管",
                "business_ready_persistence_pending": "已自动接管",
                "external_condition_required": "等待外部条件",
                "user_trigger_required": "等待用户触发",
                "canonical_persistence_confirmed": "本事项已闭环",
                "closed_requires": "existing canonical Decision Fact persistence/readback/acceptance confirmed for this same decision identity",
                "rule": "BUSINESS_DECISION_READY makes the complete business reply eligible but never implies canonical closure. While persistence/readback/acceptance is pending or failed, the user-visible execution status must not be 本事项已闭环. Persistence failure is disclosed separately and repaired from the same immutable Source.",
            },
            "canonical_identity_change_tense_contract": {
                "objects": ["Observation ADMIT", "Observation RETAIN", "Observation EXIT", "other canonical identity/state changes"],
                "before_persistence_confirmed": "Describe the business judgment as 本次判断要求/拟/应, not as an already-persisted canonical state change.",
                "after_persistence_confirmed": "Completed-state wording is allowed only after same-decision canonical persistence/readback confirms the change.",
                "rule": "A Business Decision Source contains the judgment; canonical identity/state ownership remains with the existing deterministic projection and single writer.",
            },
            "summary_must_preserve": [
                "DATA_AS_OF_BEIJING",
                "RISK_PERMISSION",
                "MAIN_CANDIDATE_AND_OPPORTUNITY_STATUS",
                "NEXT_UNIT_CAPITAL_USE",
                "AMOUNT_AND_ACTION",
                "EVERY_ACTUAL_POSITION_ACTION_WITH_RELEASE_DESTINATION_AND_CHANGE_CONDITION",
                "CAPITAL_EFFICIENCY_AND_POST_ACTION_CASH",
                "THREE_LAYER_VISIBLE_SUMMARY_LAYER_1_EXTERNAL_CROSS_MARKET",
                "THREE_LAYER_VISIBLE_SUMMARY_LAYER_2_A_SHARE_INTERNAL_FEEDBACK",
                "THREE_LAYER_VISIBLE_SUMMARY_LAYER_3_ETF_OBJECT_OPPORTUNITY_CAPITAL_EVIDENCE",
                "THREE_LAYER_DECISION_CHANGING_DELTAS",
                "FIVE_DELTAS_NEW_STRONGER_WEAKER_INVALID_NEXT_UNIT_USE",
                "CURRENT_MAX_RISK",
                "JUDGMENT_ERROR_CAUSES",
                "DECISION_RELEVANT_INFORMATION_GAPS",
                "MOST_FRAGILE_ASSUMPTION",
                "POTENTIALLY_OVERESTIMATED_OVERSEAS_INFORMATION",
                "OVERSEAS_VS_A_SHARE_DIVERGENCE",
                "NEXT_STAGE",
                "EXECUTION_STATUS",
                "NEXT_FOCUS",
                "CONFIDENCE",
                "MOST_LIKELY_ERROR_POINT",
            ],
            "three_layer_visible_summary_contract": {
                "required": True,
                "section_title": "【三层监测】",
                "layer_1_external_cross_market": "Keep a compact visible Layer 1 with only decision-relevant external/cross-market facts, their actual market phase/data-time semantics, and how they affect this decision.",
                "layer_2_a_share_internal_feedback": "Keep a compact visible Layer 2 with decision-relevant A-share breadth/style/industry/structure/liquidity evidence and explicitly state whether A-share feedback confirms, rejects, amplifies, weakens, or diverges from Layer 1.",
                "layer_3_etf_object_opportunity_capital_evidence": "Keep a compact visible Layer 3 with ETF/object-level opportunity and capital-evidence deltas needed to explain the decision. Do not repeat per-position final actions, the full Discovery/Observation disposition list, or the full capital competition; those remain in their dedicated downstream sections.",
                "degradation_rule": "If any layer is degraded/unavailable, state the localized degradation and whether it changes the decision; never translate an empty/failed optional interface into no risk/no flow/no opportunity.",
                "anti_duplication_rule": "Visible three-layer monitoring is an evidence summary only. It must not recreate Discovery, Observation management, Position Review, or Capital Competition as duplicate business stages.",
            },
            "compression_rule": "Compress raw provider/plugin dumps and unchanged evidence, not the three visible layer identities or business conclusions. Every actual holding remains individually visible in its dedicated section. The three-layer section preserves compact decision-relevant evidence and deltas without duplicating downstream Discovery, position actions, or capital competition.",
            "projection_rule": "The user-visible reply must faithfully project the same request-bound Business Decision Source and must not introduce a different candidate, action, amount, capital destination, holding disposition, or next-unit capital use.",
            "semantic_payload_contract": {
                "source_of_business_judgment": "same request-bound BUSINESS_DECISION_SOURCE after deterministic projection",
                "required_from_projected_source": [
                    "RISK_PERMISSION",
                    "MAIN_CANDIDATE_AND_OPPORTUNITY_STATUS",
                    "NEXT_UNIT_CAPITAL_USE",
                    "AMOUNT_AND_ACTION",
                    "EVERY_ACTUAL_POSITION_ACTION_WITH_RELEASE_DESTINATION_AND_CHANGE_CONDITION",
                    "CAPITAL_EFFICIENCY_AND_POST_ACTION_CASH",
                    "DECISION_EVIDENCE_CONSUMPTION",
                ],
                "required_from_request_bound_context": [
                    "DATA_AS_OF_BEIJING",
                    "THREE_LAYER_VISIBLE_SUMMARY",
                    "FIVE_DELTAS",
                    "CURRENT_MAX_RISK",
                    "JUDGMENT_ERROR_CAUSES",
                    "DECISION_RELEVANT_INFORMATION_GAPS",
                    "MOST_FRAGILE_ASSUMPTION",
                    "POTENTIALLY_OVERESTIMATED_OVERSEAS_INFORMATION",
                    "OVERSEAS_VS_A_SHARE_DIVERGENCE",
                    "NEXT_STAGE",
                    "NEXT_FOCUS",
                    "CONFIDENCE",
                    "MOST_LIKELY_ERROR_POINT",
                ],
                "execution_status_source": "execution_status_contract",
                "canonical_identity_tense_source": "canonical_identity_change_tense_contract",
                "rule": "This is a compact semantic payload contract for the ChatGPT consumer, not a renderer, report, second decision engine, state, writer, or persistence prerequisite. Business action semantics must come from the same request-bound projected Business Decision Source; contextual explanation remains actor-authored from the same request-bound evidence. The consumer may compress wording but must not omit, contradict, or independently mutate these semantics.",
            },
        },
        "provenance_rule": "Every projected fact is copied from the existing canonical request/account/current/decision/quote/discovery inputs; this packet is not a new fact owner.",
        "pit_rule": "Formal reasoning may continue when formal_analysis_availability is AVAILABLE or DEGRADED, honoring local limitations. Exact amount/share/action canonical completion requires formal_action_readiness=READY. Downstream projections cannot mutate the same PIT decision.",
        "decision_evidence_consumption_contract": {
            "required_in_business_decision_source": True,
            "request_id": request_id,
            "required_domains": [
                "layer_1_external_cross_market",
                "layer_2_a_share_internal",
                "layer_3_etf_opportunity_capital",
            ],
            "required_chain_attestations": [
                "discovery_to_capital_competition_consumed",
                "all_managed_positions_sell_chain_consumed",
                "held_etf_additional_capital_consumed",
                "next_unit_capital_use_consumed",
            ],
            "rule": "The Formal Decision actor must explicitly record what it consumed for this request. This is a consumer-completeness contract, not a second decision engine and not permission for machine-generated investment judgments.",
        },
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


def _latency_observation_role(request: dict) -> str:
    source = str(request.get("source") or request.get("requested_by") or "").strip().upper()
    request_type = str(request.get("request_type") or "").strip().upper()
    parent = str(request.get("parent_request_id") or "").strip()
    if request_type == "STATE_SYNC_ONLY" or source == "CHATGPT_MANUAL_FORMAL_COMPLETION" or parent:
        return "DOWNSTREAM_COMPLETION_NOT_DECISION_REQUEST"
    return "FORMAL_DECISION_REQUEST"


def _original_decision_request_identity(request: dict) -> str:
    role = _latency_observation_role(request)
    if role == "DOWNSTREAM_COMPLETION_NOT_DECISION_REQUEST":
        return str(request.get("parent_request_id") or "").strip() or "UNKNOWN"
    return _stable_manual_request_identity(request)


def build_fast_path_latency(request: dict, current: dict, account_or_decision: dict, decision_or_quote: dict, market_quote_or_reply: dict | str = "", reply_ready: str = "", root: Path | None = None) -> dict:
    """Report only observed timestamps; missing instrumentation stays explicit."""
    # Preserve the established five-argument replay shape
    # (request, current, decision, market_quote, reply_ready) while accepting
    # the production shape that also supplies account facts.
    if "generated_at" in account_or_decision or "generated_at_beijing" in account_or_decision:
        account, decision, market_quote = {}, account_or_decision, decision_or_quote
        reply_ready = str(market_quote_or_reply or reply_ready)
    else:
        account, decision, market_quote = account_or_decision, decision_or_quote, market_quote_or_reply
    if isinstance(market_quote, str):
        reply_ready, market_quote = market_quote, {}
    t0 = _request_received_at_beijing(request)
    ingress = request.get("request_bound_at_beijing") or request.get("canonical_ingress_at_beijing") or ""
    manual_request_identity = _stable_manual_request_identity(request)
    observation_role = _latency_observation_role(request)
    original_decision_request_identity = _original_decision_request_identity(request)
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
        "latency_observation_role": observation_role,
        "original_decision_request_identity": original_decision_request_identity,
        "may_measure_original_decision_latency": observation_role == "FORMAL_DECISION_REQUEST",
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
        "request_bound_at": request.get("request_bound_at_beijing") or ("UNKNOWN" if not request.get("request_id") else t0 or "UNKNOWN"),
        "pit_ready_at": t_new if (freshness.get("resolved_post_request") or freshness.get("post_request")) else "UNKNOWN",
        "core_result_at": t_decision if t_decision else "UNKNOWN",
        "request_to_core_result_latency": _duration_seconds(t0, t_decision) if observation_role == "FORMAL_DECISION_REQUEST" else None,
        "request_to_canonical_ingress_latency": _duration_seconds(t0, ingress),
        "canonical_ingress_to_decision_latency": _duration_seconds(ingress, t_decision) if observation_role == "FORMAL_DECISION_REQUEST" else None,
        "decision_latency_excludes_completion": True,
        "latency_status": ("OBSERVED" if t0 and t_decision else "INSTRUMENTATION_INCOMPLETE") if observation_role == "FORMAL_DECISION_REQUEST" else "DOWNSTREAM_NOT_DECISION_LATENCY",
        "latency_contract": "Only the original Formal Decision Request may measure REQUEST_RECEIVED_TO_CORE_RESULT; downstream completion/projection rebuilds must not redefine or masquerade as original decision latency.",
    }

def _business_decision_source_observation(root: Path, parent_request_id: str) -> dict:
    """Observe the earliest durable Business Decision Source for this parent.

    This is observability only. It never changes decision readiness or waits for
    canonical projection/acceptance. Product/model phases that the repository
    cannot observe remain explicit UNKNOWN.
    """
    if not parent_request_id:
        return {
            "status": "UNKNOWN",
            "requested_at_beijing": "UNKNOWN",
            "request_id": "UNKNOWN",
            "role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
        }
    request_dir = root / "requests" / "live_snapshot"
    candidates = []
    for path in request_dir.glob("*.json") if request_dir.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("request_type") or "").upper() != "BUSINESS_DECISION_SOURCE":
            continue
        if str(payload.get("parent_request_id") or "").strip() != parent_request_id:
            continue
        stamp = str(payload.get("requested_at_beijing") or "").strip()
        if stamp:
            candidates.append((stamp, str(payload.get("request_id") or path.stem)))
    if not candidates:
        return {
            "status": "UNKNOWN",
            "requested_at_beijing": "UNKNOWN",
            "request_id": "UNKNOWN",
            "role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
        }
    stamp, request_id = min(candidates, key=lambda item: item[0])
    return {
        "status": "OBSERVED",
        "requested_at_beijing": stamp,
        "request_id": request_id,
        "role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
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
    # A caller that did not supply a durable request envelope is not a new
    # manual Formal Decision at T.  Never borrow the newest historical request
    # timestamp here: that can make old request-scoped facts look post-request
    # for a new user interaction.  Non-request builds may still evaluate current
    # state, but formal request identity/time must come from --request-file.
    # Freshness assurance is the first decision-critical operation.
    build_started = time.monotonic()
    quote_started = time.monotonic()
    explicit_review_symbols = []
    for item in request_payload.get("observation_identity_review_targets") or []:
        if isinstance(item, dict):
            code = str(item.get("code") or "").upper().replace(".SH", "").replace(".SZ", "").strip()
            if len(code) == 6 and code.isdigit():
                explicit_review_symbols.append(code)
    effective_requested_symbols = list(dict.fromkeys([*(requested_symbols or []), *explicit_review_symbols]))
    market_quote = build_market_quote_context(root, force_refresh=force_refresh, requested_symbols=effective_requested_symbols, decision_request_time=request_time)
    quote_elapsed = round(time.monotonic() - quote_started, 3)
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
    universe_identity = _discovery_universe_identity(etf_universe)
    trading_calendar = read_json(root / CANONICAL_FILES["trading_calendar"], {})
    freshness = evaluate_freshness(current, policy)
    market_domain_projection = build_market_domain_projection(current, overseas_context, us_extended, freshness)
    trading_day_status = current_trading_day_status(trading_calendar)
    user_visible_time_anchor = user_visible_trading_time_anchor(datetime.now(SHANGHAI).date(), trading_calendar)
    account_gate = account_gate_status(current, account, policy)
    managed_etf_codes = {str(x.get("code") or "") for x in (etf_universe.get("objects") or []) if x.get("code")}
    managed_etf_codes.update(
        str(x.get("code") or x.get("symbol") or x.get("security_code") or "")
        for x in (account.get("positions") or [])
        if x.get("code") or x.get("symbol") or x.get("security_code")
    )
    held_etf_codes = {str(x) for x in active_account_asset_codes(root, account).get("etf", set())}
    formal_discovery = {"status": "NOT_REQUESTED", "candidates": []}
    request_source = str(request_payload.get("source") or request_payload.get("requested_by") or "").strip().upper()
    request_intent = str(request_payload.get("intent") or request_payload.get("query_intent") or "").strip().upper()
    manual_formal_sources = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "CHATGPT_USER_CONTINUE",
        "CHATGPT_USER_GITHUB_INTRADAY",
        "CHATGPT_USER_GITHUB_DECISION",
        "CHATGPT_USER_REQUEST",
        "CHATGPT_USER_INTERACTION",
    }
    manual_formal_intents = {"FORMAL_INTRADAY_DECISION", "FORMAL_INTRADAY_ANALYSIS", "EXPLICIT_LATEST"}
    formal_node_discovery_required = bool(
        request_file
        and request_source in manual_formal_sources
        and request_intent in manual_formal_intents
    )
    # if force_refresh or request_file or run_discovery: formal requests enter
    # the existing Discovery owner; legal same-node reuse is resolved below.
    should_run_discovery = bool(force_refresh or run_discovery or formal_node_discovery_required)
    discovery_pipeline_started = time.monotonic() if should_run_discovery else None
    candidate_quote_elapsed = 0.0
    if should_run_discovery:
        # force_refresh refreshes request-bound market facts; it must not make
        # a still-valid Discovery evidence identity ineligible for reuse.
        # Discovery reuse remains guarded by market node, universe identity,
        # decision-relevant delta and the existing terminal READY contract.
        formal_discovery = _reusable_formal_discovery(root, current, universe_identity, managed_etf_codes)
        if formal_discovery is None:
            formal_discovery = discover_formal_candidates(
                root,
                market_date=str(current.get("market_date") or trading_day_status.get("market_date") or ""),
                managed_codes=managed_etf_codes,
                held_codes=held_etf_codes,
            )
            formal_discovery["universe_identity"] = universe_identity
            formal_discovery["discovery_delta_identity"] = _discovery_delta_identity(root)
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
            discovered_set = {x.upper() for x in discovered_codes}
            # Preserve both request-bound existing quotes and the refreshed
            # missing subset before assigning per-candidate formal status.
            candidate_quote_union = _merge_discovery_candidate_quotes(discovered_codes, market_quote, candidate_quote)
            formal_discovery = attach_formal_quotes(formal_discovery, candidate_quote_union)
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
        discovery_latency["reuse_mode"] = (formal_discovery.get("reuse") or {}).get("mode", "FRESH_DISCOVERY")
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
    decision_context_started = time.monotonic()
    decision = build_decision_context(
        root,
        formal_discovery=formal_discovery,
        decision_request_time=request_time,
        market_quote_context=market_quote,
    )
    decision_context_elapsed = round(time.monotonic() - decision_context_started, 3)
    generated_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    fact_pack_started = time.monotonic()
    fact_pack = build_decision_fact_pack(root, request_payload, current, account, decision, market_quote, formal_discovery=formal_discovery)
    fact_pack_elapsed = round(time.monotonic() - fact_pack_started, 3)
    latency = build_fast_path_latency(request_payload, current, account, decision, market_quote, generated_at, root)
    bds = _business_decision_source_observation(root, str(request_payload.get("request_id") or "").strip()) if latency.get("may_measure_original_decision_latency") else {
        "status": "NOT_APPLICABLE",
        "requested_at_beijing": "UNKNOWN",
        "request_id": "UNKNOWN",
        "role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
    }
    latency["user_visible_decision_waterfall"] = {
        "request_received_at_beijing": latency.get("manual_request_received_at") or "UNKNOWN",
        "canonical_ingress_at_beijing": request_payload.get("request_bound_at_beijing") or request_payload.get("canonical_ingress_at_beijing") or "UNKNOWN",
        "first_qualified_market_fact_as_of_beijing": latency.get("first_qualified_market_fact_as_of") or "UNKNOWN",
        "minimum_legal_inputs_ready_at_beijing": latency.get("minimum_legal_inputs_ready_at") or "UNKNOWN",
        "business_decision_ready_at_beijing": latency.get("core_result_at") or "UNKNOWN",
        "business_decision_source": bds,
        "request_to_business_decision_ready_seconds": latency.get("request_to_core_result_latency"),
        "business_decision_ready_to_bds_seconds": (
            _duration_seconds(str(latency.get("core_result_at") or ""), str(bds.get("requested_at_beijing") or ""))
            if bds.get("status") == "OBSERVED" else None
        ),
        "business_decision_source_to_user_visible_delivery_seconds": None,
        "user_visible_delivery_at_beijing": "UNKNOWN",
        "plugin_research_wall_clock_seconds": None,
        "chatgpt_business_reasoning_wall_clock_seconds": None,
        "user_execution_interval_seconds": None,
        "user_execution_is_system_decision_latency": False,
        "unknown_reason": "Product delivery, hidden model reasoning, and plugin orchestration boundaries are not repository-observable; UNKNOWN is preserved rather than inferred.",
        "role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
    }
    latency["in_process_stage_durations_seconds"] = {
        "freshness_assurance_and_quote_context": quote_elapsed,
        "discovery_pipeline": (formal_discovery.get("latency_observability") or {}).get("discovery_pipeline_elapsed_seconds"),
        "decision_context_build": decision_context_elapsed,
        "decision_fact_pack_build": fact_pack_elapsed,
        "query_context_build_total": round(time.monotonic() - build_started, 3),
    }
    current_workflow_run_id = str(os.environ.get("GITHUB_RUN_ID") or "").strip()
    runtime_workflow_run_id = str(runtime_health.get("workflow_run_id") or runtime_health.get("run_id") or "").strip()
    runtime_matches_current = bool(current_workflow_run_id and runtime_workflow_run_id == current_workflow_run_id)
    if current_workflow_run_id:
        workflow_runtime_observation = {
            "run_started_at_beijing": (runtime_health.get("run_started_at") or runtime_health.get("started_at") or "UNKNOWN") if runtime_matches_current else "UNKNOWN",
            "core_snapshot_finished_at_beijing": (runtime_health.get("captured_at_beijing") or runtime_health.get("finished_at") or "UNKNOWN") if runtime_matches_current else "UNKNOWN",
            "workflow_run_id": current_workflow_run_id,
            "runtime_health_workflow_run_id": runtime_workflow_run_id or "UNKNOWN",
            "correlation_status": "MATCHED_CURRENT_RUN" if runtime_matches_current else "RUNTIME_HEALTH_NOT_CURRENT_RUN",
            "role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
        }
    else:
        workflow_runtime_observation = {
            "run_started_at_beijing": runtime_health.get("run_started_at") or runtime_health.get("started_at") or "UNKNOWN",
            "core_snapshot_finished_at_beijing": runtime_health.get("captured_at_beijing") or runtime_health.get("finished_at") or "UNKNOWN",
            "workflow_run_id": runtime_workflow_run_id or "UNKNOWN",
            "runtime_health_workflow_run_id": runtime_workflow_run_id or "UNKNOWN",
            "correlation_status": "CURRENT_RUN_ID_UNAVAILABLE",
            "role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
        }
    latency["workflow_runtime_observation"] = workflow_runtime_observation
    latency["waterfall_rule"] = "Persist only directly observed repository/workflow boundaries and measured in-process durations; UNKNOWN remains explicit. Hidden model reasoning is never logged or inferred."
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
        "trading_day_status": trading_day_status, "user_visible_time_anchor": user_visible_time_anchor, "runtime_health": runtime_health,
        "system_consistency_status": consistency.get("status", "MISSING"), "system_consistency_hard_errors": consistency.get("hard_error_count", None),
        "etf_universe_count": len(etf_universe.get("objects") or []), "etf_universe_identity": universe_identity, "formal_discovery_candidate_count": len(formal_discovery.get("candidates") or []), "overseas_context_status": overseas_context.get("quality_status", "MISSING"),
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

