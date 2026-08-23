from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

CANONICAL_FILES = {
    "index": "ETF_SYSTEM_INDEX.md",
    "master": "ETF规则_MASTER.md",
    "dashboard": "ETF当前状态_DASHBOARD.md",
    "experience": "ETF交易复盘与经验库_2026.md",
    "market_archive": "ETF市场行情档案_2026.md",
    "data_standard": "ETF与市场监测数据接口使用规范.md",
    "current": "data/state/CURRENT.json",
    "account_fact": "data/state/account_fact.json",
    "asset_roles": "data/state/asset_roles.json",
    "etf_monitor_universe": "config/market/etf_monitor_universe.json",
    "stock_context": "data/state/stock_context.json",
    "stock_market_context": "data/state/stock_market_context.json",
    "stock_monitor_policy": "config/market/stock_monitor_policy.json",
    "market_delta": "data/state/market_delta.json",
    "overseas_context": "data/state/overseas_context.json",
    "runtime_health": "data/state/runtime_health.json",
    "runtime_policy": "config/runtime_policy.json",
    "system_consistency": "data/state/system_consistency.json",
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
    if age <= fresh_max:
        status = "FRESH"
    elif age <= degraded_max:
        status = "DEGRADED"
    else:
        status = "STALE"
    return {"status": status, "age_seconds": age, "fresh_max_age_seconds": fresh_max, "degraded_max_age_seconds": degraded_max}


def account_gate_status(current: dict, account: dict, policy: dict) -> dict:
    raw_valid = account.get("status") == "VALID"
    market_date = current.get("market_date", "")
    updated = parse_time(account.get("updated_at", ""))
    updated_market_date = updated.astimezone(SHANGHAI).date().isoformat() if updated else ""
    same_day_required = bool(policy.get("account_fact_same_market_date_required", True))
    same_day = bool(market_date and updated_market_date == market_date)
    usable = raw_valid and (same_day or not same_day_required)
    reason = "OK" if usable else (
        "ACCOUNT_NOT_VALID" if not raw_valid else
        "ACCOUNT_FACT_NOT_CURRENT_MARKET_DATE"
    )
    return {
        "raw_status": account.get("status", "MISSING"),
        "updated_at": account.get("updated_at", ""),
        "updated_market_date": updated_market_date,
        "current_market_date": market_date,
        "same_market_date_required": same_day_required,
        "can_use_current_account_fact": usable,
        "requires_user_broker_screenshot": not usable,
        "reason": reason,
        "rule": "正式盘中/盘后决策使用的账户、持仓、现金和成交事实必须属于当前market_date；旧日VALID不得自动沿用为当日VALID。",
    }


def build_read_plan(current: dict, account: dict, policy: dict, freshness: dict) -> dict:
    latest_snapshot = current.get("latest_snapshot", "")
    account_gate = account_gate_status(current, account, policy)
    required = [
        CANONICAL_FILES["index"],
        CANONICAL_FILES["master"],
        CANONICAL_FILES["data_standard"],
        CANONICAL_FILES["current"],
        CANONICAL_FILES["system_consistency"],
        CANONICAL_FILES["runtime_policy"],
        CANONICAL_FILES["runtime_health"],
        CANONICAL_FILES["etf_monitor_universe"],
        CANONICAL_FILES["overseas_context"],
        CANONICAL_FILES["stock_monitor_policy"],
        CANONICAL_FILES["stock_context"],
        CANONICAL_FILES["stock_market_context"],
    ]
    if latest_snapshot:
        required.append(latest_snapshot)
    required.append(CANONICAL_FILES["market_delta"])
    required.append(CANONICAL_FILES["dashboard"])
    if account_gate["can_use_current_account_fact"]:
        required.extend([CANONICAL_FILES["account_fact"], CANONICAL_FILES["asset_roles"]])

    return {
        "mode": "LATEST_STATE_ON_DEMAND",
        "required_reads": required,
        "conditional_reads": {
            "lifecycle_or_prior_case_needed": CANONICAL_FILES["experience"],
            "historical_market_fact_needed": CANONICAL_FILES["market_archive"],
            "industry_chain_stock_needed": "按当前ETF/行业假设临时发现A股、美股及其他市场最有解释力的产业链公司；不使用永久固定名单。",
        },
        "account_gate": account_gate,
        "market_gate": {
            "node_status": current.get("node_status", ""),
            "latest_valid_node": current.get("latest_valid_node", ""),
            "latest_snapshot": latest_snapshot,
            "captured_at": current.get("captured_at", ""),
            "market_delta": CANONICAL_FILES["market_delta"],
            "overseas_context": CANONICAL_FILES["overseas_context"],
            "etf_monitor_universe": CANONICAL_FILES["etf_monitor_universe"],
            "stock_context": CANONICAL_FILES["stock_context"],
            "stock_market_context": CANONICAL_FILES["stock_market_context"],
            "system_consistency": CANONICAL_FILES["system_consistency"],
            "data_standard": CANONICAL_FILES["data_standard"],
            "runtime_health": CANONICAL_FILES["runtime_health"],
            "runtime_policy": CANONICAL_FILES["runtime_policy"],
            "freshness_at_context_build": freshness,
            "data_freshness": current.get("data_freshness", {}),
            "query_time_rule": "每次ChatGPT查询必须用当前时间减CURRENT.captured_at重新计算数据年龄；不得仅沿用文件内旧FRESH标签。FRESH可用于当前行情判断；DEGRADED只作背景/连续性复核，涉及当前机会、金额或卖出动作时优先等待下一有效脉冲或结合用户当前截图；STALE不得冒充实时行情。",
            "consistency_rule": "正式分析前读取system_consistency.json；若存在硬一致性FAIL，先处理系统冲突，不得把不一致的数据/名单当作完整生产状态。WARNING类账户缺失不阻断行情采集，但必须遵守账户事实门禁。",
            "data_standard_rule": "行情与监测数据的来源、质量、脉冲、新鲜度、跨市场时点和降级边界以一级目录ETF与市场监测数据接口使用规范.md为基础规范；该规范不产生交易权限。",
            "etf_rule": "ETF层机器采集对象以config/market/etf_monitor_universe.json为唯一运行清单；持仓/观察身份由Dashboard和当日账户事实解释，禁止采集脚本私自维护第二份ETF名单。",
            "overseas_rule": "正式海外与亚洲指数层必须检查NDX、SOX、N225、KOSPI、TWII、HSTECH，并完成跨市场时点对齐。",
            "stock_rule": "第三层默认个股监测由当日account_fact中的实际非ETF持仓动态生成；已确认IPO_BASE_STOCK进入打新底仓监测，未知角色个股只标记待确认。产业链观察个股按当前分析需要动态发现，可覆盖A股、美股及其他已核验市场，不维护固定三星/海力士或其他永久名单；产业链个股只作背景和传导证据。",
            "rule": "盘中查询使用最新有效状态和相邻行情变化，不绑定旧固定截图节点；数据不足时明确不足。",
        },
        "runtime_resilience": {
            "target_cadence_seconds": policy.get("target_cadence_seconds", 600),
            "fresh_max_age_seconds": policy.get("fresh_max_age_seconds", 900),
            "degraded_max_age_seconds": policy.get("degraded_max_age_seconds", 1500),
            "close_grace_seconds": policy.get("close_grace_seconds", 900),
            "principle": "采集频率是目标，不是决策时钟；延迟或失败时保留上一有效CURRENT，不用失败数据覆盖；收盘任务允许宽限补采。",
        },
    }


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
    policy = read_json(root / CANONICAL_FILES["runtime_policy"], {})
    runtime_health = read_json(root / CANONICAL_FILES["runtime_health"], {})
    overseas_context = read_json(root / CANONICAL_FILES["overseas_context"], {})
    stock_context = read_json(root / CANONICAL_FILES["stock_context"], {})
    stock_market_context = read_json(root / CANONICAL_FILES["stock_market_context"], {})
    consistency = read_json(root / CANONICAL_FILES["system_consistency"], {})
    etf_universe = read_json(root / CANONICAL_FILES["etf_monitor_universe"], {})
    freshness = evaluate_freshness(current, policy)
    account_gate = account_gate_status(current, account, policy)
    return {
        "generated_at": now_utc(),
        "market_date": current.get("market_date", ""),
        "latest_valid_node": current.get("latest_valid_node", ""),
        "current": current,
        "decision_context": decision,
        "decision_read_plan": build_read_plan(current, account, policy, freshness),
        "canonical_files": CANONICAL_FILES,
        "data_status": current.get("data_freshness", {}),
        "freshness_at_context_build": freshness,
        "runtime_health": runtime_health,
        "system_consistency_status": consistency.get("status", "MISSING"),
        "system_consistency_hard_errors": consistency.get("hard_error_count", None),
        "etf_universe_count": len(etf_universe.get("objects") or []),
        "overseas_context_status": overseas_context.get("quality_status", "MISSING"),
        "stock_context_status": stock_context.get("account_fact_status", "MISSING"),
        "stock_market_context_status": stock_market_context.get("quality_status", "MISSING"),
        "stock_role_confirmation_needed": stock_context.get("needs_role_confirmation", False),
        "account_fact_status": account["status"],
        "account_gate": account_gate,
        "needs_account_screenshot": not account_gate["can_use_current_account_fact"],
        "read_only": True,
        "interaction_boundary": "用户主动查询时先核对系统一致性和数据接口规范，再读取最新有效状态、ETF层、动态个股层、正式海外与亚洲指数层、运行健康状态与正式文件；本文件只组织读取，不生成交易动作。",
    }


def main() -> None:
    context = build(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "query_context.json", context)
    print(json.dumps({
        "ok": True,
        "market_date": context["market_date"],
        "latest_valid_node": context["latest_valid_node"],
        "system_consistency_status": context["system_consistency_status"],
        "etf_universe_count": context["etf_universe_count"],
        "account_fact_status": context["account_fact_status"],
        "account_usable": context["account_gate"]["can_use_current_account_fact"],
        "freshness": context["freshness_at_context_build"]["status"],
        "overseas_context_status": context["overseas_context_status"],
        "stock_context_status": context["stock_context_status"],
        "stock_market_context_status": context["stock_market_context_status"],
        "stock_role_confirmation_needed": context["stock_role_confirmation_needed"],
        "read_plan_mode": context["decision_read_plan"]["mode"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
