from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current

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
}


def build_read_plan(current: dict, account: dict) -> dict:
    latest_snapshot = current.get("latest_snapshot", "")
    account_valid = account.get("status") == "VALID"
    required = [
        CANONICAL_FILES["index"],
        CANONICAL_FILES["master"],
        CANONICAL_FILES["current"],
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
            "market_delta": CANONICAL_FILES["market_delta"],
            "data_freshness": current.get("data_freshness", {}),
            "rule": "盘中查询使用最新有效状态和相邻行情变化，不绑定旧固定截图节点；数据不足时明确不足。",
        },
    }


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
    return {
        "generated_at": now_utc(),
        "market_date": current.get("market_date", ""),
        "latest_valid_node": current.get("latest_valid_node", ""),
        "current": current,
        "decision_context": decision,
        "decision_read_plan": build_read_plan(current, account),
        "canonical_files": CANONICAL_FILES,
        "data_status": current.get("data_freshness", {}),
        "account_fact_status": account["status"],
        "needs_account_screenshot": account["status"] != "VALID",
        "read_only": True,
        "interaction_boundary": "用户主动查询时读取最新有效状态、相邻行情变化与正式文件；本文件只组织读取，不生成交易动作。",
    }


def main() -> None:
    context = build(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "query_context.json", context)
    print(json.dumps({
        "ok": True,
        "market_date": context["market_date"],
        "latest_valid_node": context["latest_valid_node"],
        "account_fact_status": context["account_fact_status"],
        "read_plan_mode": context["decision_read_plan"]["mode"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
