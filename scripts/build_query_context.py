from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current, read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()

CANONICAL_FILES = {
    "index": "ETF_SYSTEM_INDEX.md",
    "master": "ETF规则_MASTER.md",
    "dashboard": "ETF当前状态_DASHBOARD.md",
    "experience": "ETF交易复盘与经验库_2026.md",
    "market_archive": "ETF市场行情档案_2026.md",
    "current": "data/state/CURRENT.json",
    "account_fact": "data/state/account_fact.json",
    "market_delta": "data/state/market_delta.json",
    "runtime_health": "data/state/runtime_health.json",
    "runtime_policy": "config/runtime_policy.json",
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


def build_read_plan(current: dict, account: dict, policy: dict, freshness: dict) -> dict:
    latest_snapshot = current.get("latest_snapshot", "")
    account_valid = account.get("status") == "VALID"
    required = [
        CANONICAL_FILES["index"],
        CANONICAL_FILES["master"],
        CANONICAL_FILES["current"],
        CANONICAL_FILES["runtime_policy"],
        CANONICAL_FILES["runtime_health"],
    ]
    if latest_snapshot:
        required.append(latest_snapshot)
    required.append(CANONICAL_FILES["market_delta"])
    required.append(CANONICAL_FILES["dashboard"])
    if account_valid:
        required.append(CANONICAL_FILES["account_fact"])

    return {
        "mode": "LATEST_STATE_ON_DEMAND",
        "required_reads": required,
        "conditional_reads": {
            "lifecycle_or_prior_case_needed": CANONICAL_FILES["experience"],
            "historical_market_fact_needed": CANONICAL_FILES["market_archive"],
        },
        "account_gate": {
            "status": account.get("status", "MISSING"),
            "can_use_current_account_fact": account_valid,
            "requires_user_broker_screenshot": not account_valid,
            "rule": "缺少当日账户、持仓、现金或成交事实时，不得根据旧Dashboard推定没有变化。",
        },
        "market_gate": {
            "node_status": current.get("node_status", ""),
            "latest_valid_node": current.get("latest_valid_node", ""),
            "latest_snapshot": latest_snapshot,
            "captured_at": current.get("captured_at", ""),
            "market_delta": CANONICAL_FILES["market_delta"],
            "runtime_health": CANONICAL_FILES["runtime_health"],
            "runtime_policy": CANONICAL_FILES["runtime_policy"],
            "freshness_at_context_build": freshness,
            "data_freshness": current.get("data_freshness", {}),
            "query_time_rule": "每次ChatGPT查询必须用当前时间减CURRENT.captured_at重新计算数据年龄；不得仅沿用文件内旧FRESH标签。FRESH可用于当前行情判断；DEGRADED只作背景/连续性复核，涉及当前机会、金额或卖出动作时优先等待下一有效脉冲或结合用户当前截图；STALE不得冒充实时行情。",
            "rule": "盘中查询使用最新有效状态和相邻行情变化，不绑定旧固定截图节点；数据不足时明确不足。",
        },
        "runtime_resilience": {
            "target_cadence_seconds": policy.get("target_cadence_seconds", 600),
            "fresh_max_age_seconds": policy.get("fresh_max_age_seconds", 900),
            "degraded_max_age_seconds": policy.get("degraded_max_age_seconds", 1500),
            "principle": "采集频率是目标，不是决策时钟；延迟或失败时保留上一有效CURRENT，不用失败数据覆盖。",
        },
    }


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
    policy = read_json(root / CANONICAL_FILES["runtime_policy"], {})
    runtime_health = read_json(root / CANONICAL_FILES["runtime_health"], {})
    freshness = evaluate_freshness(current, policy)
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
        "account_fact_status": account["status"],
        "needs_account_screenshot": account["status"] != "VALID",
        "read_only": True,
        "interaction_boundary": "用户主动查询时读取最新有效状态、相邻行情变化、运行健康状态与正式文件；本文件只组织读取，不生成交易动作。",
    }


def main() -> None:
    context = build(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "query_context.json", context)
    print(json.dumps({
        "ok": True,
        "market_date": context["market_date"],
        "latest_valid_node": context["latest_valid_node"],
        "account_fact_status": context["account_fact_status"],
        "freshness": context["freshness_at_context_build"]["status"],
        "read_plan_mode": context["decision_read_plan"]["mode"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
