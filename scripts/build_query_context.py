from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
    from market_quote_router import build_market_quote_context
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
    from scripts.market_quote_router import build_market_quote_context

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
            "etf_rule": "ETF机器采集以etf_monitor_universe.json为唯一运行清单；持仓/观察身份由Dashboard和账户事实解释。",
            "overseas_rule": "正式海外/亚洲指数必须检查NDX、SOX、N225、KOSPI、TWII、HSTECH；北京时间08:00起已有日韩市场脉冲，不能等A股9:30才开始读取海外。",
            "us_extended_hours_rule": "美国信息分三段解释：上一正式现金盘（NDX/SOX）、POST_MARKET（QQQ/SOXX及条件个股）、下一交易日PRE_MARKET。A股早盘前可能获得上一美股盘后信息；下一美股PRE_MARKET通常在北京时间A股收盘后开始，主要形成下一A股交易日的前置信号。扩展时段不得等同正式指数确认。",
            "stock_rule": "第三层默认个股由当前有效账户事实动态生成；产业链个股按查询主题动态发现。",
            "rule": "正式当前查询优先补采当前可得行情，再使用最近有效状态补充连续性；数据不足时明确不足。",
        },
        "runtime_resilience": {"target_cadence_seconds": policy.get("target_cadence_seconds", 600), "fresh_max_age_seconds": policy.get("fresh_max_age_seconds", 900), "degraded_max_age_seconds": policy.get("degraded_max_age_seconds", 1500), "close_grace_seconds": policy.get("close_grace_seconds", 900), "principle": "查询时立即补采优先；常规10分钟只是生产目标。补采失败才回退最近有效状态，并按查询时刻重新判定新鲜度。"},
    }


def build(root: Path = ROOT, *, force_refresh: bool = False, requested_symbols: list[str] | None = None, request_file: str | None = None) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
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
    trading_day_status = current_trading_day_status(trading_calendar)
    account_gate = account_gate_status(current, account, policy)
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
    request_time = None
    if request_file:
        request = read_json((root / request_file).resolve(), {})
        request_time = parse_time(request.get("requested_at_beijing") or request.get("request_time"))
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
    market_quote = build_market_quote_context(root, force_refresh=force_refresh, requested_symbols=requested_symbols, decision_request_time=request_time)
    return {
        "generated_at": now_utc(), "generated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "market_date": current.get("market_date", ""), "latest_valid_node": current.get("latest_valid_node", ""),
        "rules_version": decision.get("rules_version", ""), "current": current, "decision_context": decision,
        "analysis_coverage": decision.get("analysis_coverage", {}),
        "etf_strategy_risk_metrics": decision.get("etf_strategy_risk_metrics", {}),
        "data_quality_summary": decision.get("data_quality_summary", {}),
        "point_in_time": decision.get("point_in_time", {}),
        "scheduled_pulse_health": decision.get("scheduled_pulse_health", {}),
        "formal_action": decision.get("formal_action", {}),
        "decision_read_plan": build_read_plan(current, account, policy, freshness),
        "market_quote_router": market_quote,
        "system_objects": system_objects, "user_requested_objects": [],
        "canonical_files": CANONICAL_FILES, "data_status": {**(current.get("data_freshness") or {}), **freshness}, "freshness_at_context_build": freshness,
        "interactive_decision_freshness": market_quote.get("decision_freshness", {}),
        "trading_day_status": trading_day_status, "runtime_health": runtime_health,
        "system_consistency_status": consistency.get("status", "MISSING"), "system_consistency_hard_errors": consistency.get("hard_error_count", None),
        "etf_universe_count": len(etf_universe.get("objects") or []), "overseas_context_status": overseas_context.get("quality_status", "MISSING"),
        "overseas_generated_at_beijing": overseas_context.get("generated_at_beijing", ""),
        "us_extended_hours_status": us_extended.get("quality_status", "MISSING"), "us_extended_hours_generated_at_beijing": us_extended.get("generated_at_beijing", ""),
        "stock_context_status": stock_context.get("account_fact_status", "MISSING"), "stock_market_context_status": stock_market_context.get("quality_status", "MISSING"),
        "stock_role_confirmation_needed": stock_context.get("needs_role_confirmation", False), "account_fact_status": account["status"], "account_gate": account_gate,
        "needs_account_screenshot": not account_gate["can_use_current_account_fact"], "read_only": True,
        "interaction_boundary": "用户主动查询时先核对一致性与交易日历，再按‘查询时立即补采 → 最近一次有效快照 → 明确降级/缺失’获取行情；正式输出必须标注北京时间真实数据时点，并区分美股现金盘、盘后和盘前。",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--request-file", default="", help="explicit live_snapshot request payload for this analysis")
    args = parser.parse_args()
    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]
    context = build(ROOT, force_refresh=args.force_refresh, requested_symbols=symbols, request_file=args.request_file or None)
    atomic_json_write(ROOT / "data" / "state" / "query_context.json", context)
    print(json.dumps({"ok": True, "generated_at_beijing": context["generated_at_beijing"], "market_date": context["market_date"], "latest_valid_node": context["latest_valid_node"], "system_consistency_status": context["system_consistency_status"], "candidate_trading_day": context["trading_day_status"]["is_candidate_trading_day"], "etf_universe_count": context["etf_universe_count"], "account_fact_status": context["account_fact_status"], "account_usable": context["account_gate"]["can_use_current_account_fact"], "freshness": context["freshness_at_context_build"]["status"], "overseas_context_status": context["overseas_context_status"], "us_extended_hours_status": context["us_extended_hours_status"], "stock_context_status": context["stock_context_status"], "stock_market_context_status": context["stock_market_context_status"], "read_plan_mode": context["decision_read_plan"]["mode"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()


