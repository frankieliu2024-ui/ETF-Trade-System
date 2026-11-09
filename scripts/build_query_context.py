Warning: truncated output (original token count: 37838)
Total output lines: 2333

from __future__ import annotations

import argparse
import hashlib
import subprocess
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
    from formal_etf_opportunity_discovery import attach_formal_quotes
    from trading_time_semantics import user_visible_trading_time_anchor
    from pulse_history import read_pulse_projection
    from hyperliquid_contribution import build_contribution
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
    from scripts.build_stock_context import active_account_asset_codes
    from scripts.market_quote_router import build_market_quote_context
    from scripts.formal_etf_opportunity_discovery import attach_formal_quotes
    from scripts.trading_time_semantics import user_visible_trading_time_anchor
    from scripts.pulse_history import read_pulse_projection
    from scripts.hyperliquid_contribution import build_contribution

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

CANONICAL_FILES = {
    "index": "ETF_SYSTEM_INDEX.md", "master": "ETF规则_MASTER.md", "dashboard": "ETF当前状态_DASHBOARD.md",
    "experience": "ETF交易复盘与经验库_2026.md", "market_archive": "ETF市场行情档案_2026.md",
    "data_standard": "ETF与市场监测数据接口使用规范.md", "current": "data/state/CURRENT.json",
    "account_fact": "data/state/account_fact.json", "asset_roles": "data/state/asset_roles.json",
    "etf_monitor_universe": "config/market/etf_monitor_universe.json", "observation_thesis_registry": "config/market/etf_observation_thesis_registry.json", "trading_calendar": "config/market/a_share_trading_calendar_2026.json",
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


def _hyperliquid_decision_evidence(external: dict, current: dict) -> dict:
    """Classify continuous Hyperliquid state against the latest A-share fact."""
    observations = [x for x in (external.get("observations") or []) if isinstance(x, dict)]
    a_share_time = str(current.get("captured_at_beijing") or (current.get("data_freshness") or {}).get("captured_at_beijing") or current.get("captured_at") or "")

    def parse(value: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    a_dt = parse(a_share_time)
    def number(value: object) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if result == result and abs(result) != float("inf") else None

    items: list[dict] = []
    families: dict[str, dict] = {}
    for item in observations:
        ctx = item.get("context") or {}
        mark = number(ctx.get("markPx")); prev = number(ctx.get("prevDayPx"))
        funding = number(ctx.get("funding")); premium = number(ctx.get("premium"))
        collected = str(item.get("collected_at_beijing") or "")
        c_dt = parse(collected)
        delta = abs((c_dt - a_dt).total_seconds()) if c_dt and a_dt else None
        direction = "SUPPORTIVE" if mark is not None and prev not in (None, 0) and mark > prev else ("NEGATIVE" if mark is not None and prev not in (None, 0) and mark < prev else "NEUTRAL")
        crowding = "ELEVATED" if abs(funding or 0) >= 0.0001 or abs(premium or 0) >= 0.002 else "NORMAL"
        family = next((str(m.get("family") or "") for m in (external.get("mapping") or []) if str(m.get("asset") or "") == str((item.get("asset") or {}).get("name") or "")), "")
        evidence = {
            "asset": (item.get("asset") or {}).get("name", ""), "dex": item.get("dex"), "family": family,
            "direction": direction, "crowding": crowding, "funding": funding, "premium": premium,
            "mark": mark, "oracle": number(ctx.get("oraclePx")), "mid": number(ctx.get("midPx")),
            "open_interest": number(ctx.get("openInterest")), "day_notional_volume": number(ctx.get("dayNtlVlm")),
            "collected_at_beijing": collected, "a_share_fact_as_of_beijing": a_share_time,
            "time_delta_seconds": round(delta, 3) if delta is not None else None,
            "time_relation": "ALIGNED_WINDOW" if delta is not None and delta <= 900 else ("ASYNC_LEAD_OR_LAG" if delta is not None else "A_SHARE_TIME_UNAVAILABLE"),
            "evidence_role": "CONTINUOUS_MARKET_STATE_BOUNDARY",
            "decision_effect": "SUPPORT_AND_TIMING" if direction == "SUPPORTIVE" and crowding == "NORMAL" else "TIMING_FILTER" if direction == "SUPPORTIVE" else "CONTRADICTION_AND_CROWDING" if direction == "NEGATIVE" and crowding == "ELEVATED" else "CONTRADICTION" if direction == "NEGATIVE" else "NO_INCREMENTAL_VALUE",
        }
        items.append(evidence)
        if family:
            bucket = families.setdefault(family, {"assets": [], "supportive": 0, "negative": 0, "elevated_crowding": 0})
            bucket["assets"].append(evidence["asset"])
            bucket["supportive"] += int(direction == "SUPPORTIVE")
            bucket["negative"] += int(direction == "NEGATIVE")
            bucket["elevated_crowding"] += int(crowding == "ELEVATED")
    return {
        "status": "READY" if items and str(external.get("quality_status") or "").upper() in {"PASS", "DEGRADED"} else "MISSING",
        "collection_time_is_pit_boundary": True, "a_share_fact_as_of_beijing": a_share_time,
        "observation_count": len(items), "items": items, "family_summary": families,
        "decision_use": ["direction", "entry_timing", "condition_filter", "crowding", "capital_competition"],
        "decision_boundary": "Hyperliquid evidence can change object-level opportunity strength, timing, holding management and capital competition; it cannot independently grant Trial, Confirm, amount, sell quantity or order.",
    }


def _same_node_persisted_formal_discovery(root: Path, current: dict, universe_identity: str) -> dict | None:
    """Return an existing valid Discovery instead of treating a downstream rebuild as absence.

    Query/state rebuilds may not run Discovery.  In that case they must carry
    forward the latest terminal same-node result; writing NOT_REQUESTED over a
    valid result would make a later Formal Decision consume a false absence.
    """
    sources: list[dict] = []
    projection = read_json(root / "data" / "state" / "formal_discovery_current.json", {})
    if isinstance(projection, dict):
        sources.append(projection)
    query = read_json(root / "data" / "state" / "query_context.json", {})
    query_discovery = query.get("formal_etf_discovery") if isinstance(query, dict) else None
    if isinstance(query_discovery, dict):
        sources.append(query_discovery)

    current_market_date = str(current.get("market_date") or "")
    current_node = str(current.get("latest_valid_node") or "")
    current_snapshot = str(current.get("latest_snapshot") or "")
    current_delta = _discovery_delta_identity(root)
    for discovery in sources:
        if str(discovery.get("status") or "").upper() != "READY":
            continue
        if str(discovery.get("market_date") or "") != current_market_date:
            continue
        if str(discovery.get("latest_valid_node") or "") != current_node:
            continue
        discovery_snapshot = str(discovery.get("latest_snapshot") or "")
        canonical_node_producer = (
            str(discovery.get("formation_rule") or "") == "EVERY_VERIFIED_A_SHARE_PULSE"
            or "VERIFIED_NODE_OVERLAY" in str(discovery.get("source") or "")
        )
        if discovery_snapshot and current_snapshot and discovery_snapshot != current_snapshot:
            # The close producer intentionally does not rewrite CURRENT.  Its
            # verified same-day close artifact is therefore authoritative for
            # this legal node even when CURRENT still points at an older
            # snapshot from the same market date.
            if not (canonical_node_producer and current_node == "close"):
                continue
        # The market-snapshot producer has already completed the canonical
        # Discovery pipeline for this exact verified node.  A downstream
        # query-context rebuild must consume that result as-is; requiring a
        # second delta/universe projection here can make the same close pulse
        # fall through to a fresh provider run and overwrite valid candidates
        # with an unrelated empty result.
        if (
            discovery_snapshot
            and current_snapshot
            and discovery_snapshot == current_snapshot
            and canonical_node_producer
        ):
            return dict(discovery)
        discovery_universe = str(discovery.get("universe_identity") or "")
        if universe_identity and discovery_universe and discovery_universe != universe_identity:
            continue
        discovery_delta = str(discovery.get("discovery_delta_identity") or "")
        if discovery_delta and current_delta and discovery_delta != current_delta:
            continue
        return dict(discovery)
    return None

def _reusable_formal_discovery(root: Path, current: dict, universe_identity: str, managed_codes: set[str]) -> dict | None:
    """Reuse a terminal result only when it belongs to the same legal market node."""
    discovery = _same_node_persisted_formal_discovery(root, current, universe_identity)
    if discovery is None:
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


def _refresh_reused_discovery_external_evidence(discovery: dict, external: dict | None) -> dict:
    """Refresh Hyperliquid evidence on a reused Discovery without rerunning Discovery."""
    if not external:
        return discovery
    try:
        from formal_etf_opportunity_discovery import _apply_external_driver_triggers
    except ModuleNotFoundError:
        from scripts.formal_etf_opportunity_discovery import _apply_external_driver_triggers

    candidates = _apply_external_driver_triggers(list(discovery.get("candidates") or []), external)
    refreshed = dict(discovery)
    refreshed_candidates = []
    for candidate in candidates:
        item = dict(candidate)
        evidence = list(item.get("_external_driver_triggers") or [])
        if evidence:
            item["external_driver_evidence"] = evidence
            classes = list(item.get("information_classes") or [])
            if "EXTERNAL_DRIVER_TRIGGER" not in classes:
                classes.append("EXTERNAL_DRIVER_TRIGGER")
            item["information_classes"] = classes
        item.pop("_external_driver_triggers", None)
        refreshed_candidates.append(item)
    refreshed["candidates"] = refreshed_candidates
    return refreshed


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
    market_date = str(current.get("market_date") or "")
    account_fact_market_date = str(
        account.get("last_confirmed_market_date")
        or account.get("account_fact_market_date")
        or account.get("market_date")
        or ""
    )
    updated = parse_time(account.get("updated_at", ""))
    updated_calendar_date = updated.astimezone(SHANGHAI).date().isoformat() if updated else ""
    same_day_required = bool(policy.get("account_fact_same_market_date_required", False))
    same_fact_day = bool(market_date and account_fact_market_date and account_fact_market_date == market_date)
    usable = raw_valid and (same_fact_day or not same_day_required)
    if not raw_valid:
        reason = "ACCOUNT_NOT_VALID"
    elif same_day_required and not same_fact_day:
        reason = "ACCOUNT_FACT_NOT_CURRENT_MARKET_DATE"
    else:
        reason = "OK"
    if raw_valid and account_fact_market_date and market_date and account_fact_market_date != market_date:
        account_age_status = "ACCOUNT_FACT_OLDER_THAN_MARKET_FACT"
    elif raw_valid:
        account_age_status = "CURRENT_OR_UNDATED_ACCOUNT_FACT"
    else:
        account_age_status = "ACCOUNT_FACT_UNAVAILABLE"
    return {
        "raw_status": account.get("status", "MISSING"),
        "updated_at": account.get("updated_at", ""),
        "updated_calendar_date": updated_calendar_date,
        "account_fact_market_date": account_fact_market_date,
        "current_market_date": market_date,
        "same_market_date_required": same_day_required,
        "same_market_date": same_fact_day,
        "account_age_status": account_age_status,
        "can_use_current_account_fact": usable,
        "requires_user_broker_screenshot": not usable,
        "reason": reason,
        "rule": "账户事实日期、账户事实更新时间和行情事实日期必须分别保存；账户事实不会因行情自动刷新而自动推进。未取得新账户事实时，可按有效性规则使用最近已确认账户事实，并显式披露其日期和年龄。"
    }


def consistency_projection_status(root: Path = ROOT) -> dict:
    """Classify the persisted report as current evidence or last-known history.

    A persisted PASS/WARNING may only represent current consistency when its
    checked commit is the current checkout.  A stale FAIL keeps its severity,
    but is labelled last-known rather than silently promoted to current proof.
    """
    report = read_json(root / CANONICAL_FILES["system_consistency"], {})
    status = str(report.get("status") or "MISSING").upper()
    checked = str((report.get("commit_audit") or {}).get("checked_commit") or "")
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        head = ""
    identity_matches = bool(checked and head and checked == head)
    current_evidence = identity_matches and status in {"PASS", "WARNING", "FAIL"}
    return {
        "path": CANONICAL_FILES["system_consistency"],
        "reported_status": status,
        "checked_commit": checked,
        "current_head": head,
        "identity_matches_current_head": identity_matches,
        "evidence_semantic": "CURRENT_ACCEPTANCE_PROJECTION" if current_evidence else "LAST_KNOWN_PROJECTION",
        "can_prove_current_consistency": current_evidence,
        "effective_current_status": status if current_evidence else "UNKNOWN",
        "last_known_failure_preserved": status == "FAIL",
        "rule": "stale PASS/WARNING cannot prove current consistency; stale FAIL remains visible as last-known failure without masquerading as current acceptance",
    }


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
    registry = read_json(root / CANONICAL_FILES["observation_thesis_registry"], {})
    registry_objects = registry.get("objects") if isinstance(registry, dict) else {}
    if not isinstance(registry_objects, dict):
        registry_objects = {}
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
    # A formally admitted observation can predate the current event schema.  The
    # canonical registry is the durable identity source for that case; it only
    # supplies the six thesis fields and never creates an admission or trading
    # decision.  Event data remains authoritative whenever it is complete.
    for code in observation_codes:
        if code in out:
            continue
        candidate = registry_objects.get(code)
        if not isinstance(candidate, dict):
            continue
        if all(str(candidate.get(key) or "").strip() for key in required):
            out[code] = {key: candidate.get(key) for key in required}
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
        if…21838 tokens truncated…ive_snapshot"
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
    discovery_pulse_index = read_pulse_projection(root, "DISCOVERY")
    hyperliquid_pulse_index = read_pulse_projection(root, "HYPERLIQUID")
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
    formal_discovery = _same_node_persisted_formal_discovery(root, current, universe_identity) or {"status": "NOT_REQUESTED", "candidates": []}
    weekly_return_attribution = read_json(
        root / "data" / "state" / "weekly_return_attribution.json",
        {
            "schema_version": "1.0",
            "layer": "WEEKLY_RETURN_ATTRIBUTION",
            "status": "NOT_AVAILABLE",
            "strict_decomposition_status": "NOT_COMPLETE",
            "read_only": True,
        },
    )
    request_source = str(request_payload.get("source") or request_payload.get("requested_by") or "").strip().upper()
    request_intent = _request_query_intent(request_payload)
    manual_formal_sources = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
        "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
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
        formal_discovery = _refresh_reused_discovery_external_evidence(
            formal_discovery, overseas_context.get("hyperliquid")
        ) if formal_discovery is not None else None
        if formal_discovery is None:
            # Discovery has one producer: build_weekly_discovery.py, which
            # consumes a verified A-share node.  Query-context construction is
            # a consumer only; it must not create a second provider-backed
            # result that can overwrite the canonical node result with an
            # unrelated empty projection.
            formal_discovery = {
                "status": "NOT_RESOLVED",
                "candidates": [],
                "candidate_count": None,
                "coverage_status": "NOT_AVAILABLE",
                "empty_reason": "DISCOVERY_PRODUCER_RESULT_NOT_AVAILABLE",
                "producer_required": True,
                "consumption_boundary": "QUERY_CONTEXT_CONSUMER_ONLY",
            }
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
    fact_pack = build_decision_fact_pack(root, request_payload, current, account, decision, market_quote, formal_discovery=formal_discovery, external_driver_context=overseas_context.get("hyperliquid"))
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
    hyperliquid_source = overseas_context.get("hyperliquid") if isinstance(overseas_context.get("hyperliquid"), dict) else {}
    hyperliquid_provenance = {
        "context_id": hyperliquid_source.get("context_id") or "",
        "collection_started_at_utc": hyperliquid_source.get("collection_started_at_utc") or "",
        "collection_finished_at_utc": hyperliquid_source.get("collection_finished_at_utc") or "",
        "earliest_observation_collected_at_utc": hyperliquid_source.get("earliest_observation_collected_at_utc") or "",
        "latest_observation_collected_at_utc": hyperliquid_source.get("latest_observation_collected_at_utc") or "",
        "observation_count": hyperliquid_source.get("observation_count", len(hyperliquid_source.get("observations") or [])),
        "provider_timestamp_status": "NOT_PROVIDED_BY_ENDPOINT" if hyperliquid_source.get("observations") else "MISSING",
        "time_basis": hyperliquid_source.get("time_basis") or "COLLECTION_FINISHED_TIME_AS_VISIBLE_STATE_BOUNDARY",
        "decision_use": "AUXILIARY_ONLY; collection time is not event time",
    }
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
        "discovery_pulse_index": discovery_pulse_index if isinstance(discovery_pulse_index, dict) else {},
        "hyperliquid_pulse_index": hyperliquid_pulse_index if isinstance(hyperliquid_pulse_index, dict) else {},
        "continuous_pulse_consumption": {
            "discovery_latest": (discovery_pulse_index or {}).get("latest") if isinstance(discovery_pulse_index, dict) else None,
            "hyperliquid_latest": (hyperliquid_pulse_index or {}).get("latest") if isinstance(hyperliquid_pulse_index, dict) else None,
            "rule": "决策上下文消费最新合法对象投影及其同链脉冲索引；脉冲索引仅提供可追溯事实序列，不自行产生交易许可。"
        },
        "weekly_return_attribution": weekly_return_attribution,
        "canonical_files": CANONICAL_FILES, "data_status": {**(current.get("data_freshness") or {}), **freshness}, "freshness_at_context_build": freshness,
        "interactive_decision_freshness": market_quote.get("decision_freshness", {}),
        "trading_day_status": trading_day_status, "user_visible_time_anchor": user_visible_time_anchor, "runtime_health": runtime_health,
        "system_consistency_status": consistency.get("status", "MISSING"), "system_consistency_hard_errors": consistency.get("hard_error_count", None),
        "etf_universe_count": len(etf_universe.get("objects") or []), "etf_universe_identity": universe_identity, "formal_discovery_candidate_count": len(formal_discovery.get("candidates") or []), "overseas_context_status": overseas_context.get("quality_status", "MISSING"),
        "overseas_generated_at_beijing": overseas_context.get("generated_at_beijing", ""),
        "hyperliquid_provenance": hyperliquid_provenance,
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



def validate_request_bound_context(context: dict, request_file: str | None) -> None:
    """Fail closed when a Formal Decision context is empty or bound to another request."""
    if not request_file:
        return
    request_path = (ROOT / request_file).resolve()
    if not request_path.is_file():
        raise RuntimeError(f"request-bound context source missing: {request_file}")
    try:
        from state_manager import read_json_checked
    except ModuleNotFoundError:
        from scripts.state_manager import read_json_checked
    request, read_status = read_json_checked(request_path, {})
    if read_status != "READ_OK" or not isinstance(request, dict) or not request:
        raise RuntimeError(
            f"Formal Decision request-bound source read status={read_status}: {request_file}"
        )
    request_id = str(request.get("request_id") or "").strip()
    source = str(request.get("source") or request.get("requested_by") or "").strip().upper()
    formal_sources = {
        "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
        "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
    }
    if source not in formal_sources:
        return
    if not request_id:
        raise RuntimeError("Formal Decision request is missing request_id")
    if not isinstance(context, dict) or not context:
        raise RuntimeError(f"Formal Decision query_context is empty for request_id={request_id}")
    pack = context.get("decision_fact_pack") if isinstance(context.get("decision_fact_pack"), dict) else {}
    trigger = pack.get("trigger") if isinstance(pack.get("trigger"), dict) else {}
    bound_id = str(trigger.get("request_id") or "").strip()
    if bound_id != request_id:
        raise RuntimeError(
            f"Formal Decision query_context request binding mismatch: expected={request_id} actual={bound_id or '<empty>'}"
        )
    work_package = pack.get("decision_work_package") if isinstance(pack.get("decision_work_package"), dict) else {}
    if not isinstance(work_package.get("problem_graph"), list) or not work_package.get("problem_graph"):
        raise RuntimeError(f"Formal Decision query_context missing problem_graph for request_id={request_id}")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--request-file", default="", help="explicit live_snapshot request payload for this analysis")
    parser.add_argument("--run-discovery", action="store_true", help="run bounded all-market Discovery from an already-current production snapshot without forcing a second core refresh")
    args = parser.parse_args()
    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]
    context = build(ROOT, force_refresh=args.force_refresh, requested_symbols=symbols, request_file=args.request_file or None, run_discovery=args.run_discovery)
    validate_request_bound_context(context, args.request_file or None)
    _write_formal_layer_projections(ROOT, read_current(ROOT), context.get("formal_etf_discovery") or {})
    atomic_json_write(ROOT / "data" / "state" / "query_context.json", context)
    print(json.dumps({"ok": True, "generated_at_beijing": context["generated_at_beijing"], "market_date": context["market_date"], "latest_valid_node": context["latest_valid_node"], "system_consistency_status": context["system_consistency_status"], "candidate_trading_day": context["trading_day_status"]["is_candidate_trading_day"], "etf_universe_count": context["etf_universe_count"], "account_fact_status": context["account_fact_status"], "account_usable": context["account_gate"]["can_use_current_account_fact"], "freshness": context["freshness_at_context_build"]["status"], "overseas_context_status": context["overseas_context_status"], "us_extended_hours_status": context["us_extended_hours_status"], "stock_context_status": context["stock_context_status"], "stock_market_context_status": context["stock_market_context_status"], "read_plan_mode": context["decision_read_plan"]["mode"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

