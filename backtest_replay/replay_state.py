from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.state_manager import append_event, build_dashboard_candidate, build_decision_context, read_current, update_current


SCENARIOS = [
    {"name": "普通交易日", "date": "2026-08-24", "node": "10:30", "status": "READY"},
    {"name": "上涨节点", "date": "2026-08-24", "node": "11:30", "status": "READY", "market_note": "price_up"},
    {"name": "下跌节点", "date": "2026-08-24", "node": "13:30", "status": "READY", "market_note": "price_down"},
    {"name": "发生成交节点", "date": "2026-08-24", "node": "14:30", "status": "READY", "trade": True},
    {"name": "账户事实缺失节点", "date": "2026-08-24", "node": "close", "status": "READY", "account_missing": True},
    {"name": "旧通知进入节点", "date": "2026-08-24", "node": "11:30", "status": "READY", "old_node": "10:30"},
]


def run() -> dict:
    results = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "system").mkdir()
        (root / "data" / "state").mkdir(parents=True)
        (root / "system" / "ETF当前状态_DASHBOARD.md").write_text("manual dashboard", encoding="utf-8")
        (root / "data" / "state" / "account_fact.json").write_text(json.dumps({"status": "MISSING", "source": "BROKER_SCREENSHOT", "updated_at": "", "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []}), encoding="utf-8")
        update_current(root=root, market_date="", node="", captured_at="", node_status="NON_TRADING_DAY")
        for index, scenario in enumerate(SCENARIOS, 1):
            if scenario.get("old_node"):
                update_current(root=root, market_date=scenario["date"], node=scenario["old_node"], captured_at=f'{scenario["date"]}T10:30:00+08:00')
            update_current(root=root, market_date=scenario["date"], node=scenario["node"], captured_at=f'{scenario["date"]}T{scenario["node"].replace(":", "")}00+08:00', node_status=scenario["status"])
            if scenario.get("trade"):
                append_event(root=root, event_type="TRADE_EXECUTED", source="replay_fixture", payload={"fixture": scenario["name"], "fact_only": True}, git_commit="replay")
            current = read_current(root)
            candidate = build_dashboard_candidate(root)
            context = build_decision_context(root)
            results.append({"scenario": scenario["name"], "latest_node": current["latest_valid_node"], "node_status": current["node_status"], "account_status": context["account_fact_status"], "needs_account_update": candidate["needs_account_update"], "trade_decision_generated": False})
    return {"replay": "PASS", "scenario_count": len(results), "results": results}


if __name__ == "__main__":
    result = run()
    output = Path(__file__).resolve().parent / "phase2_replay_result.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
