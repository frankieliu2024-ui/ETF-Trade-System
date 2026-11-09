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
        family = next((str(m.get("family") or "") for m in (external.get("mapping") or []) if isinstance(m, dict) and str(m.get("asset") or "") == str((item.get("asset") or {}).get("name") or "")), "")
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
    current_snapshot_payload = read_json(root / current_snapshot, {}) if current_snapshot else {}
    current_fact_time = str(
        current_snapshot_payload.get("formed_at_beijing")
        or current_snapshot_payload.get("fact_time_beijing")
        or current_snapshot_payload.get("captured_at_beijing")
        or ""
    )
    current_delta = _discovery_delta_identity(root)
    for discovery in sources:
        if str(discovery.get("status") or "").upper() != "READY":
            continue
        if str(discovery.get("market_date") or "") != current_market_date:
            continue
        if str(discovery.get("latest_valid_node") or "") != current_node:
            continue
        discovery_snapshot = str(discovery.get("latest_snapshot") or "")
        discovery_fact_time = str(discovery.get("generated_at_beijing") or discovery.get("formed_at_beijing") or "")
        if current_fact_time and discovery_fact_time and current_node != "close":
            try:
                current_dt = datetime.fromisoformat(current_fact_time.replace("Z", "+00:00"))
                discovery_dt = datetime.fromisoformat(discovery_fact_time.replace("Z", "+00:00"))
                if current_dt.tzinfo is None:
                    current_dt = current_dt.replace(tzinfo=SHANGHAI)
                if discovery_dt.tzinfo is None:
                    discovery_dt = discovery_dt.replace(tzinfo=SHANGHAI)
                if (current_dt - discovery_dt).total_seconds() > 15 * 60:
                    continue
            except ValueError:
                pass
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



def _build_hyperliquid_object_bindings(external: dict, positions: list[dict], observation_inputs: list[dict], discovery_inputs: list[dict]) -> list[dict]:
    """Expose mapped Hyperliquid evidence to every eligible ETF object layer.

    This is a read-only evidence map. It does not promote an object or grant
    Trial/Confirm/amount/sell authority.
    """
    mapping = external.get("mapping") or []
    observed = {((item.get("asset") or {}).get("name", "") if isinstance(item.get("asset"), dict) else str(item.get("asset") or "")): item for item in (external.get("observations") or []) if isinstance(item, dict)}
    known = {}
    for item in [*positions, *observation_inputs, *discovery_inputs]:
        if isinstance(item, dict):
            code = str(item.get("code") or item.get("symbol") or "").strip()
            if code:
                known[code] = item.get("name") or code
    bindings = []
    seen: set[tuple[str, str]] = set()
    for item in mapping:
        if not isinstance(item, dict):
            continue
        assets = [str(item.get("asset") or "")]
        evidence = [observed[a] for a in assets if a in observed]
        codes = [str(code).strip() for code in (item.get("etf_codes") or []) if str(code).strip()]
        markers = [str(marker).strip() for marker in (item.get("name_markers") or []) if str(marker).strip()]
        # Explicit ETF-code mappings remain authoritative. For mapped economic
        # families without an explicit code list, expose the same auxiliary
        # evidence to known holding, observation and Discovery objects using
        # the configured name markers. This is evidence attachment only: it
        # does not create candidates or grant Trial/Confirm authority.
        for obj in [*positions, *observation_inputs, *discovery_inputs]:
            if not isinstance(obj, dict):
                continue
            code = str(obj.get("code") or obj.get("symbol") or "").strip()
            name = str(obj.get("name") or "")
            if code and name and markers and any(marker in name for marker in markers):
                codes.append(code)
        for code in codes:
            key = (code, ",".join(assets))
            if key in seen:
                continue
            seen.add(key)
            bindings.append({
                "code": code,
                "name": known.get(code, ""),
                "external_assets": assets,
                "observation_count": len(evidence),
                "quality_status": external.get("quality_status") or "MISSING",
                "eligible_object_layers": ["DISCOVERY", "OBSERVATION", "OPPORTUNITY", "HOLDING", "EXECUTED_TRIAL"],
                "evidence_role": "AUXILIARY_OBJECT_LEVEL_SUPPORT_OR_CONTRADICTION",
                "decision_boundary": "辅助证据可影响对象级强弱、时机、持有管理和资本竞争；不得单独授予Trial、Confirm、金额、卖出份额或订单。",
            })
    return bindings



def _cross_engine_validation(binding: dict, object_inputs: list[dict], decision_evidence: dict[str, dict] | None = None) -> dict:
    """Relate Hyperliquid evidence to the same A-share Discovery object.

    This is a bidirectional validation record only; it never changes object
    identity, candidate admission, or trading authority.
    """
    code = str(binding.get("code") or "")
    discovery = next((x for x in object_inputs if str(x.get("code") or x.get("symbol") or "") == code), {})
    asset_name = str((binding.get("external_assets") or [""])[0])
    evidence = (decision_evidence or {}).get(asset_name) or {}
    direction = str(evidence.get("direction") or binding.get("direction") or "").upper()
    effect = str(evidence.get("decision_effect") or binding.get("decision_effect") or "").upper()
    classes = list(discovery.get("information_classes") or discovery.get("_discovery_information_classes") or [])
    if effect in {"CONTRADICTION", "CONTRADICTION_AND_CROWDING"}:
        relation = "CROSS_MARKET_DIVERGENCE"
    elif direction == "SUPPORTIVE" and classes:
        relation = "EXTERNAL_CONFIRMED_A_SHARE"
    elif direction == "SUPPORTIVE":
        relation = "EXTERNAL_NOT_TRANSMITTED"
    elif classes:
        relation = "A_SHARE_NOT_CONFIRMED_EXTERNALLY"
    else:
        relation = "INSUFFICIENT_COMPARISON"
    return {
        "relation": relation,
        "discovery_object_present": bool(discovery),
        "discovery_information_classes": classes,
        "hyperliquid_decision_effect": effect or "INSUFFICIENT",
        "business_effect": "外部信号与A股对象同步，可增强方向判断" if relation == "EXTERNAL_CONFIRMED_A_SHARE" else "外部信号已出现，但A股对象尚未同步，暂不升级" if relation == "EXTERNAL_NOT_TRANSMITTED" else "A股对象与外部信号背离，降低结论强度或限制时机" if relation == "CROSS_MARKET_DIVERGENCE" else "A股对象已有变化，但外部证据尚未确认" if relation == "A_SHARE_NOT_CONFIRMED_EXTERNALLY" else "当前时点或映射不足，不能形成双向验证",
        "next_validation": "下一节点复核A股对象是否继续承接，以及外部方向、资金行为和拥挤状态是否延续或收敛",
        "authority_boundary": "仅记录Discovery与Hyperliquid的相互验证，不改变候选、持仓、观察身份或交易权限",
    }


def _decision_problem_graph(positions: list[dict], discovery_inputs: list[dict], account: dict, observation_inputs: list[dict] | None = None, observation_review_inputs: list[dict] | None = None) -> list[dict]:
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
        problems.append({"problem_id": f"HOLDING:{code}", "decision_object": code, "security": item.get("name") or code, "current_quantity": item.get("quantity"), "current_capital_occupation": item.get("market_value"), "alternatives": {"HOLD": "继续占用当前资本", "REDUCE": "部分释放资本", "EXIT": "全部释放资本"}, "capital_efficiency_release_required": True, "required_business_judgment": "比较HOLD/REDUCE/EXIT并确定当前动作、数量、去向和下一变化条件；必须把风险型卖出与资本效率型释放分开判断：未达到资产自身风险退出条件不能单独证明继续占资最优，若存在已独立成立的新机会，必须判断其优势是否足以覆盖迁移成本、失败风险、旧仓赢家右尾和账户功能损失"})
        if str(item.get("asset_type") or "").upper() == "ETF":
            problems.append({"problem_id": f"HELD_ETF_ADD:{code}", "decision_object": code, "security": item.get("name") or code, "required_business_judgment": "判断是否追加资本及相对现金/其他用途的效率"})
    for item in observation_inputs or []:
        code = str(item.get("code") or "")
        if code:
            problems.append({"problem_id": f"OBSERVATION:{code}", "decision_object": code, "security": item.get("name") or code, "thscode": item.get("thscode") or "", "existing_thesis_state": item.get("existing_thesis_state") or {}, "role_contract": "CONTINUOUS_INFORMATION_V1", "required_business_judgment": "先判断持续信息功能是否仍独立、可证伪且不可被更有效对象充分替代，再判断当前机会状态；RETAIN不要求当前接近Trial/Confirm，EXIT必须说明信息功能失效/被替代及退出后的信息连续性成本。观察ETF若当前形成机会可同时作为节点级候选参与资本比较；对已形成的机会资格逐项比较Trial、直接Confirm和0元，未达资格时说明Trial/Confirm各自的决定性缺口、可观察补齐/证伪条件和最近复核节点；不得默认0元或自动升级身份。"})
    for item in observation_review_inputs or []:
        code = str(item.get("code") or "")
        if code:
            problems.append({"problem_id": f"OBSERVATION_REVIEW:{code}", "decision_object": code, "security": item.get("name") or code, "thscode": item.get("thscode") or "", "formal_quote_status": item.get("formal_quote_status") or "", "role_contract": "EXPLICIT_OBSERVATION_IDENTITY_REVIEW_V1", "required_business_judgment": "用户显式要求重新评估已退出Observation的对象。先判断是否重新取得独立、可证伪、跨节点持续信息价值；ADMIT/REJECT只决定Observation身份，不自动产生机会、Trial/Confirm或交易动作。"})
    for item in discovery_inputs:
        code = str(item.get("code") or "")
        if code:
            problems.append({"problem_id": f"DISCOVERY:{code}", "decision_object": code, "security": item.get("name") or code, "formal_quote_status": item.get("formal_quote_status") or "", "role_contract": "NODE_LOCAL_CANDIDATE_V1", "management_identity": item.get("management_identity"), "required_business_judgment": "判断本节点机会ETF状态及资本竞争位置；机会ETF角色本身不持久。只有另有持续跨节点信息价值时才ADMIT为观察ETF，REJECT观察准入不等于否定本节点候选机会；对已形成的机会资格逐项比较Trial、直接Confirm和0元，未达资格时说明Trial/Confirm各自的决定性缺口、可观察补齐/证伪条件和最近复核节点；不得默认0元或自动升级身份"})
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
        # Every formal problem must explicitly consume the current global/cross-market
        # review. This is a review attestation, not a trade signal: it may record
        # material change, no material change, or localized degradation.
        "GLOBAL_CROSS_MARKET_REVIEW",
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
            # A holding sell-chain must see the same opportunity/capital alternatives
            # that can justify a capital-efficiency release.  Asset-self evidence
            # alone creates an incumbent bias when cash is below a qualified Trial.
            classes = shared_market + holding + opportunity + capital
        elif pid.startswith("HELD_ETF_ADD:"):
            classes = shared_market + holding + ["HOLDING_ADDITIONAL_CAPITAL", "CASH"]
        elif pid.startswith(("DISCOVERY:", "OBSERVATION:", "OBSERVATION_REVIEW:")):
            classes = shared_market + opportunity
        elif pid.startswith("BASE_STOCK_REPLACEMENT:"):
            # Replacement is a capital-use comparison: the existing contract
            # requires comparison with current base stock, ETF opportunities,
            # cash and other legally releasable capital.
            classes = shared_market + holding + opportunity + capital
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

    # The cross-market review itself is mandatory even when no single external
    # domain changes the decision. The actor must attest that the current global
    # clocks were checked and state whether the result is material, immaterial,
    # or degraded. This prevents an empty layer-1 consumption list from being
    # interpreted as "no global information exists".
    layer_1 = monitoring.get("layer_1_external_cross_market") or {}
    base_evidence = layer_1.get("base_evidence") or {}
    available["GLOBAL_CROSS_MARKET_REVIEW"] = "SATISFIED" if base_evidence else "DEGRADED"

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
        "response_contract": {"must_answer": ["final_action", "capital_comparison", "next_change_condition", "evidence_decision_impact"], "holding_additional_fields": ["capital_occupancy_reason", "higher_efficiency_alternative", "capital_efficiency_release_assessment", "quantity_if_reduce_or_exit", "capital_destination_if_reduce_or_exit", "zero_base_reentry_test", "hold_over_cash_and_eligible_opportunity_reason", "reduce_over_hold_reason", "exit_over_hold_reason"], "holding_capital_competition_policy": "RELEASE_FIRST", "holding_counterfactual_policy": "ZERO_BASE_HOLD_REDUCE_EXIT_COMPARE", "holding_default_review_state": "REDUCE_OR_EXIT_REVIEW", "holding_release_first_fields": ["strategy_reset_review", "exit_or_reduce_evidence", "holding_thesis_status", "long_term_outcome_review", "hold_over_release_reason", "zero_base_reentry_test", "hold_over_cash_and_eligible_opportunity_reason"], "opportunity_default_review_state": "TRIAL_CONFIRM_REVIEW", "opportunity_capital_competition_policy": "TRIAL_CONFIRM_ZERO_COMPARE", "opportunity_review_fields": ["opportunity_review_evidence", "trial_eligibility_and_trial_over_zero_reason", "direct_confirm_eligibility_and_confirm_over_trial_reason", "zero_deployment_decisive_reason_if_zero", "capital_alternative_if_trial_or_confirm", "no_buy_review", "opportunity_evidence", "buy_over_cash_reason", "execution_gate_reason", "observable_requalification_condition", "next_review_node"], "opportunity_nonqualification_fields": ["why_not_trial", "why_not_confirm", "why_zero_is_currently_better", "observable_requalification_condition", "next_review_node"], "main_candidate_additional_fields": ["candidate_code", "candidate_name", "opportunity_status", "opportunity_evidence", "trial_over_zero_reason", "confirm_over_trial_reason", "zero_deployment_reason_if_zero", "capital_replacement_path", "next_review_node", "buy_over_cash_reason", "execution_gate_reason"], "opportunity_additional_fields": ["disposition", "reason", "opportunity_status", "for_DISCOVERY_ADMIT_only:name,thscode,thesis,falsifier,next_decision_information,information_value_reason"], "next_unit_additional_fields": ["new_amount_yuan", "post_action_deployable_cash", "future_opportunity_capacity", "cash_opportunity_cost", "alternative_capital_use_review", "concentration_account_structure_effect", "selected_state_reason", "compared_capital_states_as_business_state_names", "zero_amount_decisive_reason_if_zero", "capital_migration_review"], "schema_knowledge_required": False, "rule": "Actor answers only business questions keyed by problem_id. Canonical lifecycle, managed reviews, opportunity reviews, capital_use and evidence-consumption nesting are machine projections."},
        "decision_marginal_stop": {
            "required_states": ["SATISFIED", "DEGRADED", "INSUFFICIENT", "NOT_REQUIRED"],
            "expansion_required": bool(unresolved), "expansion_complete": not unresolved,
            "unresolved_required_requirements": unresolved,
            "stop_rule": "继续扩展仅当未解决required evidence仍可能改变risk permission、candidate、holding action、amount、capital migration或next-unit capital use。",
        },
        "problem_evidence_index": by_problem,
    }


def _request_query_intent(request: dict) -> str:
    """Normalize current structured and legacy request intent shapes."""
    intent = request.get("intent")
    if isinstance(intent, dict):
        value = intent.get("query_intent") or intent.get("intent") or ""
    else:
        value = intent or request.get("query_intent") or ""
    return str(value).strip().upper()


def build_decision_fact_pack(root: Path, request: dict, current: dict, account: dict, decision: dict, market_quote: dict, formal_discovery: dict | None = None, external_driver_context: dict | None = None) -> dict:
    """Project one request's minimum sufficient, decision-ready PIT inputs.

    This remains an in-memory read-only projection.  It normalizes already
    qualified facts and completion obligations; it never ranks candidates or
    derives a buy/sell/capital-allocation answer.
    """
    freshness = market_quote.get("decision_freshness") or {}
    universe = read_json(root / CANONICAL_FILES["etf_monitor_universe"], {})
    discovery = formal_discovery or {}
    external_driver = external_driver_context or {}
    hyperliquid_evidence = _hyperliquid_decision_evidence(external_driver, current)
    hyperliquid_latest = read_pulse_projection(ROOT, "HYPERLIQUID").get("latest") or {}
    hyperliquid_observation_time_beijing = str(
        hyperliquid_latest.get("collected_at_beijing")
        or hyperliquid_latest.get("fact_time_beijing")
        or hyperliquid_latest.get("formed_at_beijing")
        or ""
    )
    hyperliquid_contribution = build_contribution(context={"latest": hyperliquid_latest, "observations": external_driver.get("observations") or [], "mapping": external_driver.get("mapping") or [], "generated_at_beijing": external_driver.get("collection_finished_at_utc")}, task="FORMAL_DECISION")
    hyperliquid_provenance = {
        "context_id": external_driver.get("context_id") or "",
        "collection_started_at_utc": external_driver.get("collection_started_at_utc") or "",
        "collection_finished_at_utc": external_driver.get("collection_finished_at_utc") or "",
        "earliest_observation_collected_at_utc": external_driver.get("earliest_observation_collected_at_utc") or "",
        "latest_observation_collected_at_utc": external_driver.get("latest_observation_collected_at_utc") or "",
        "visible_observation_as_of_beijing": hyperliquid_observation_time_beijing,
        "visible_observation_time_note": "Hyperliquid采集时间。",
        "observation_count": external_driver.get("observation_count", len(external_driver.get("observations") or [])),
        "provider_timestamp_status": "NOT_PROVIDED_BY_ENDPOINT",
        "time_basis": external_driver.get("time_basis") or "COLLECTION_FINISHED_TIME_AS_VISIBLE_STATE_BOUNDARY",
        "decision_use": "AUXILIARY_ONLY; collection time is not event time",
    }
    external_driver_observations = []
    for item in external_driver.get("observations") or []:
        if not isinstance(item, dict):
            continue
        asset = item.get("asset") or {}
        ctx = item.get("context") or {}
        external_driver_observations.append({
            "asset": asset.get("name") if isinstance(asset, dict) else str(asset),
            "dex": item.get("dex"), "mark": ctx.get("markPx"), "oracle": ctx.get("oraclePx"),
            "mid": ctx.get("midPx"), "funding": ctx.get("funding"), "open_interest": ctx.get("openInterest"),
            "premium": ctx.get("premium"), "day_notional_volume": ctx.get("dayNtlVlm"),
            "provider_timestamp": item.get("provider_timestamp"),
            "provider_timestamp_status": item.get("provider_timestamp_status"),
            "collected_at_utc": item.get("collected_at_utc"),
            "collected_at_beijing": item.get("collected_at_beijing"),
            "request_started_at_utc": item.get("request_started_at_utc"),
            "request_finished_at_utc": item.get("request_finished_at_utc"),
        })
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
            "external_driver_evidence": item.get("external_driver_evidence") or [],
        })

    request_id = str(request.get("request_id") or "").strip()
    requested_at = _request_received_at_beijing(request)
    request_source = str(request.get("source") or request.get("requested_by") or "").strip().upper()
    # Canonical request-bound identity values are derived once from the
    # validated parent envelope and reused by every downstream projection.
    source_value = request_source
    requested_at_value = requested_at
    entry_mode = str(request.get("entry_mode") or "").strip().upper()
    task_id_value = str(request.get("task_id") or "").strip()
    task_run_value = str(request.get("task_run_id") or "").strip()
    occurrence_value = str(request.get("occurrence_id") or "").strip()
    requested_date = str(request.get("market_date") or current.get("market_date") or "").strip()
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
    request_intent = _request_query_intent(request)
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

    # Formal Decision consumes already-published fixed-slot facts. It does not
    # create a live_snapshot request, so absent request-scoped PIT is not an
    # action blocker for this consumer. Request-bound refresh paths remain strict.
    formal_decision_run = (
        str(request.get("task_id") or "").strip() == "ETF正式决策"
        or request_source == "CHATGPT_SCHEDULED_FORMAL_ANALYSIS"
    )
    requires_request_scoped_action_pit = bool(is_manual_formal_request and not formal_decision_run)
    action_blockers = list(ingress_blockers)
    if requires_request_scoped_action_pit and not post_request:
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
        "formal_decision_fixed_slot_consumer": formal_decision_run,
        "request_scoped_action_pit_required": requires_request_scoped_action_pit,
        "missing_held_etfs": missing_held_etfs,
        "missing_account_stocks": missing_account_stocks,
        "rule": "Formal Decision consumes already-published fixed-slot facts and does not require a new request-scoped PIT; request-bound refresh paths remain fail-closed on request identity, action-qualified PIT, valid account truth, and complete market-evidence coverage for every actual positive-quantity position. This gate must not be interpreted as a ban on independent legal analysis.",
    }

    # Keep three decisions separate: whether reasoning may continue, whether
    # Trial/Confirm qualification may be assessed, and whether exact amount or
    # share execution fields are sufficiently supported.  The latter must not
    # suppress the former two.
    formal_action_readiness["analysis_ready"] = not ingress_blockers
    formal_action_readiness["trial_confirm_review_allowed"] = not ingress_blockers
    formal_action_readiness["exact_amount_share_ready"] = not action_blockers
    formal_action_readiness["local_limitations"] = list(action_blockers)
    formal_action_readiness["qualification_rule"] = (
        "Missing account intraday changes, optional market breadth, partial "
        "Discovery history, or provider-native timestamps may limit exact "
        "amount/share execution fields, but do not by themselves suppress "
        "object-level Trial/Confirm review or a management recommendation."
    )

    regime_context = read_json(root / "data" / "state" / "market_regime_context.json", {})
    structure_context = read_json(root / "data" / "state" / "market_structure_context.json", {})
    external_quality = str(external_driver.get("quality_status") or "NOT_REQUESTED").upper()
    decision_fact_availability = {
        "account": {
            "status": str(account.get("status") or "UNKNOWN").upper(),
            "as_of_beijing": account.get("updated_at") or "",
            "intraday_change_status": "CONFIRMED" if account.get("account_change_events_after_confirmed_at") else "NOT_OBSERVED",
            "orders_status": "CONFIRMED_EMPTY" if isinstance(account.get("orders"), list) and not account.get("orders") else "AVAILABLE",
            "trades_status": "AVAILABLE",
            "effect_boundary": "可用于持仓管理和可用数量；未有当日券商事实时，不把未观察成交解释成当日绝对无成交。",
        },
        "market_breadth": {
            "status": "READY" if regime_context.get("etf_breadth") else "MISSING",
            "source": "data/state/market_regime_context.json",
            "effect_boundary": "可用于市场内部反馈；不等同完整全市场资金流或盘口深度。",
        },
        "intraday_participation": {
            "status": "READY" if structure_context.get("items") else "MISSING",
            "source": "data/state/market_structure_context.json",
            "effect_boundary": "可用于对象级成交承接和路径复核；不生成独立交易许可。",
        },
        "fund_flow": {"status": "NOT_AVAILABLE_IN_CURRENT_FORMAL_FACTS", "effect_boundary": "仅降低资金流判断强度，不阻断其他对象判断。"},
        "order_book_depth": {"status": "NOT_AVAILABLE_IN_CURRENT_FORMAL_FACTS", "effect_boundary": "仅降低盘口冲击判断强度，不阻断其他对象判断。"},
        "rates_fx": {"status": "NOT_AVAILABLE_IN_CURRENT_FORMAL_FACTS", "effect_boundary": "仅影响需要汇率/利率传导的跨市场对象，不作全局阻断。"},
        "hyperliquid": {
            "status": external_quality,
            "provider_timestamp_status": "NOT_PROVIDED_BY_ENDPOINT" if external_driver.get("observations") else "MISSING",
            "context_id": hyperliquid_provenance["context_id"],
            "latest_observation_collected_at_utc": hyperliquid_provenance["latest_observation_collected_at_utc"],
            "earliest_observation_collected_at_utc": hyperliquid_provenance["earliest_observation_collected_at_utc"],
            "effect_boundary": "对象级方向、时机和拥挤过滤；不独立生成Trial、Confirm、金额或卖出份额。",
        },
        "discovery": {
            "status": discovery_status,
            "coverage_status": discovery.get("coverage_status") or "UNKNOWN",
            "candidate_count": len(discovery.get("candidates") or []),
            "effect_boundary": "部分覆盖只限制缺失对象或历史证据，不把合法候选集合解释为全市场机会为零。",
        },
        "lifecycle_and_sell_chain": {
            "status": "READY" if decision.get("lifecycle_projection") else "MISSING",
            "effect_boundary": "逐持仓影响继续持有/释放和份额链；不自动否决新机会端的独立资格。",
        },
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

    def _normalize_security_code(value: object) -> str:
        return str(value or "").upper().replace(".SH", "").replace(".SZ", "").strip()

    held_etf_codes = {
        _normalize_security_code(item.get("code"))
        for item in positions
        if str(item.get("asset_type") or "").upper() == "ETF"
        and _normalize_security_code(item.get("code"))
    }
    raw_observations = [
        item for item in (universe.get("objects") or [])
        if _normalize_security_code(item.get("code"))
        and _normalize_security_code(item.get("code")) not in held_etf_codes
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
    hyperliquid_object_context = {}
    hyperliquid_by_asset = {str(x.get("asset") or ""): x for x in external_driver_observations}
    hyperliquid_decision_by_asset = {
        str(x.get("asset") or ""): x
        for x in (hyperliquid_evidence.get("items") or [])
        if isinstance(x, dict)
    }
    for item in positions + observation_inputs + discovery_inputs:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("symbol") or "")
        name = str(item.get("name") or "")
        matches = []
        for mapping in external_driver.get("mapping") or []:
            if not isinstance(mapping, dict):
                continue
            asset_name = str(mapping.get("asset") or "")
            by_code = code in {str(x) for x in (mapping.get("etf_codes") or [])}
            by_marker = any(str(marker) and str(marker) in name for marker in (mapping.get("name_markers") or []))
            observation = hyperliquid_by_asset.get(asset_name)
            if observation and (by_code or by_marker):
                matches.append({
                    "asset": asset_name,
                    "family": mapping.get("family"),
                    "observation": observation,
                    "decision_evidence": hyperliquid_decision_by_asset.get(asset_name) or {},
                })
        if matches:
            hyperliquid_object_context[code] = {
                "code": code,
                "name": name,
                "evidence": matches,
                "decision_boundary": "对象级辅助证据；不得独立生成Trial、Confirm、金额、卖出份额或订单。",
            }

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
    # Historical base-stock screens remain research artifacts only. They do not
    # create current Formal Decision objects. Actual account stocks already enter
    # the problem graph through HOLDING:*; any future non-held stock opportunity
    # must arrive through a separately qualified current-opportunity source.
    problem_graph = _decision_problem_graph(
        positions, discovery_inputs, account, observation_inputs, observation_review_inputs,
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
    marginal_stop = work_package.get("decision_marginal_stop") or {}
    unresolved_required = list(marginal_stop.get("unresolved_required_requirements") or [])
    capital_scope_prefixes = (
        "HOLDING:", "HELD_ETF_ADD:", "BASE_STOCK_REPLACEMENT:",
        "DISCOVERY:", "OBSERVATION:", "OBSERVATION_REVIEW:",
    )
    capital_scope_ids = {
        "DEPLOYABLE_CASH", "RELEASABLE_CAPITAL", "TRIAL_CONFIRM_CAPACITY",
        "NEXT_UNIT_CAPITAL_USE", "MAIN_CANDIDATE",
    }
    capital_unresolved = [
        item for item in unresolved_required
        if (
            str(item).startswith(capital_scope_prefixes)
            or str(item) in capital_scope_ids
            or str(item).split(":", 1)[0] in capital_scope_ids
        )
    ]
    # Keep analysis admission lightweight: unresolved capital evidence is
    # observable here, while the action/amount/share decision remains subject
    # to the single unified capital-comparison contract at BDS validation.
    work_package["capital_competition_readiness"] = {
        "status": "READY" if not capital_unresolved else "EVIDENCE_INCOMPLETE",
        "ready": True,
        "hard_gate": False,
        "scope": "BUSINESS_ANALYSIS_AND_COMPLETION_VALIDATION",
        "unresolved_required_requirements": capital_unresolved,
        "rule": "资本竞争必须在正式业务判断中完成；证据不足是可披露的业务结论，不得阻断FINAL_CONTENT。完成记录仍须校验资本竞争字段完整性，字段缺失只能阻断后置投影。",
    }
    work_package["capital_competition_action_gate"] = {
        "status": "ADVISORY" if capital_unresolved else "OPEN",
        "blocks": [],
        "source": "UNIFIED_CAPITAL_COMPARISON_CONTRACT",
        "post_final_content_validation": True,
        "rule": "该投影仅标记证据完整性，不是FINAL_CONTENT前置门；BDS字段校验仍由统一BUSINESS_DECISION_SOURCE owner执行。",
    }

    work_package["decision_priority_contract"] = {
        "scope": "EXISTING_FACTS_ONLY",
        "required_focus": [
            "nearest_actionable_candidate",
            "single_decisive_candidate_blocker",
            "trial_over_zero_comparison",
            "priority_holding_action",
            "single_decisive_release_blocker",
        ],
        "allowed_actions": ["配置", "Trial", "观察", "否决", "继续持有", "追加", "部分释放", "退出评估"],
        "rule": (
            "This is a compact consumer contract, not a ranking engine or trade "
            "authority. The actor must prioritize existing qualified evidence "
            "before expanding caveats; local limitations stay attached to the "
            "affected object or execution field."
        ),
    }

    work_package["three_layer_monitoring_evidence"] = three_layer_monitoring
    work_package["previous_formal_decision_baseline"] = _latest_formal_decision_baseline(root, _request_received_at_beijing(request))
    work_package["response_contract"]["material_state_transition_attribution"] = {"required_when_changed": ["risk_permission", "main_candidate_or_opportunity_status", "holding_action"], "fields": ["material_evidence_delta", "evidence_requirement_ids"], "rule": "Material state changes versus the previous canonical Formal Decision require explicit attribution to qualified current-request evidence. Execution/market-phase or cash constraints must not mechanically rewrite orthogonal risk/opportunity states."}

    return {
        "schema_version": "1.5",
        "role": "PREFERRED_MINIMUM_SUFFICIENT_FORMAL_REASONING_INPUT",
        "trigger": {
            "source": source_value,
            "request_id": request.get("request_id") or "",
            "requested_at_beijing": requested_at_value,
            "entry_mode": entry_mode,
            "task_id": task_id_value,
            "task_run_id": task_run_value,
            "occurrence_id": occurrence_value,
            "market_date": requested_date,
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
        "hyperliquid_contribution": hyperliquid_contribution,
        "hyperliquid_external_driver_context": {
            "quality_status": external_driver.get("quality_status") or "NOT_REQUESTED",
            "pulse_continuity": read_pulse_projection(root, "HYPERLIQUID").get("continuity") or {
                "status": "MISSING",
                "rule": "Hyperliquid连续脉冲索引不可读或尚未形成，不能用旧正文补齐当前外部证据。",
            },
            "dual_engine_join_rule": {
                "key": "ETF代码",
                "requires_same_etf": True,
                "requires_discovery_evidence": True,
                "requires_hyperliquid_evidence": True,
                "allowed_outcomes": ["SUPPORT", "CONTRADICTION", "TIMING_FILTER", "CROWDING_RISK", "NO_DIRECT_MAPPING", "INSUFFICIENT"],
                "formal_use": "只有绑定同一ETF且具备当前事实的记录，才可进入该ETF的方向、时机、风险或资本比较；无绑定记录只能保留为跨市场背景。",
            },
            "dex_count": external_driver.get("dex_count"),
            "catalog_count": external_driver.get("catalog_count"),
            "focused_count": external_driver.get("focused_count"),
            "observations": external_driver_observations,
            "mapping": external_driver.get("mapping") or [],
            "object_context": hyperliquid_object_context,
            "decision_evidence": hyperliquid_evidence,
            "object_bindings": [dict(item, cross_engine_validation=_cross_engine_validation(item, [*positions, *observation_inputs, *discovery_inputs], {str(x.get("asset") or ""): x for x in hyperliquid_evidence.get("items") or []})) for item in _build_hyperliquid_object_bindings(external_driver, positions, observation_inputs, discovery_inputs)],
            "time_basis": external_driver.get("time_basis", ""),
            "decision_boundary": "可服务于Discovery、观察ETF、机会ETF、实际持仓和已执行Trial的对象级辅助证据；不得独立生成Trial、Confirm、金额、卖出份额或订单。",
            "formal_consumption_contract": {
                "required": True,
                "scope": ["DISCOVERY", "OBSERVATION", "OPPORTUNITY", "HOLDING", "EXECUTED_TRIAL"],
                "per_object_fields": ["object_code", "mapped_assets", "evidence_status", "direction", "crowding", "time_relation", "decision_effect", "business_impact"],
                "business_impact_values": ["SUPPORT", "CONTRADICTION", "TIMING_FILTER", "NO_INCREMENTAL_VALUE", "LOCAL_LIMITATION"],
                "rule": "正式决策必须逐对象说明Hyperliquid对当前对象是支持、矛盾、仅时机过滤、无增量价值或局部限制；没有映射或没有可消费观察时标记LOCAL_LIMITATION，不得写成全局无机会或全局禁止交易。",
                "authority_boundary": "该契约只证明证据被读取和解释，不授予Trial、Confirm、金额、卖出份额或订单权限。",
            },
        },
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
        "decision_fact_availability": decision_fact_availability,
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


def _write_formal_layer_projections(root: Path, current: dict, discovery: dict) -> None:
    """Consume the producer-owned Discovery projection without rewriting it.

    Discovery publication and pulse-history append are owned exclusively by
    build_weekly_discovery.py. Query-context construction is downstream
    read/consume work; it must not publish a second projection or turn a
    missing producer result into an empty candidate set.
    """
    return


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
        "formal_decision_pulse_provenance": {
            "discovery": {
                "identity": (discovery_pulse_index or {}).get("latest", {}).get("identity", "") if isinstance(discovery_pulse_index, dict) else "",
                "generated_at_beijing": formal_discovery.get("generated_at_beijing", ""),
                "latest_snapshot": formal_discovery.get("latest_snapshot", ""),
                "latest_valid_node": formal_discovery.get("latest_valid_node", ""),
                "candidate_count": formal_discovery.get("candidate_count"),
                "coverage_status": formal_discovery.get("coverage_status", ""),
                "source": formal_discovery.get("source", ""),
            },
            "hyperliquid": {
                "identity": (hyperliquid_pulse_index or {}).get("latest", {}).get("identity", "") if isinstance(hyperliquid_pulse_index, dict) else "",
                "collected_at_beijing": hyperliquid_provenance.get("latest_observation_collected_at_beijing", "") or hyperliquid_source.get("formed_at_beijing", "") or hyperliquid_source.get("fact_time_beijing", "") or hyperliquid_source.get("collected_at_beijing", ""),
                "status": hyperliquid_source.get("quality_status", ""),
                "observation_count": hyperliquid_provenance.get("observation_count"),
                "mapping_status": hyperliquid_source.get("mapping_coverage_status", ""),
                "time_basis": hyperliquid_provenance.get("time_basis", ""),
            },
            "rule": "Formal Decision必须消费本对象包中的最新合法Discovery和Hyperliquid投影，并在数据与边界披露各自北京时间时点；不得仅凭正文概括替代来源身份。"
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

