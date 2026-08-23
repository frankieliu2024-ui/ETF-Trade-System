from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.state_manager import atomic_json_write, build_decision_context, read_current, update_current


REPLAY_DATE = "2026-08-21"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
NODES = [("0925", "09:25", "ETF盘前节点提醒"), ("1030", "10:30", "ETF 10:30节点提醒"), ("1130", "11:30", "ETF 11:30节点提醒")]


def event_id(node: str, latest: str, created_at: str) -> str:
    value = f"{REPLAY_DATE}|{node}|{latest}|{created_at}".encode("utf-8")
    return "notification_" + hashlib.sha256(value).hexdigest()[:24]


def setup(root: Path, source_root: Path) -> None:
    (root / "data" / "state").mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "system").mkdir(parents=True, exist_ok=True)
    (root / "events" / "events.jsonl").write_text("", encoding="utf-8")
    shutil.copy2(source_root / "ETF当前状态_DASHBOARD.md", root / "ETF当前状态_DASHBOARD.md")
    atomic_json_write(root / "data" / "state" / "account_fact.json", {"updated_at": "", "source": "BROKER_SCREENSHOT", "status": "MISSING", "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []})
    atomic_json_write(root / "data" / "state" / "CURRENT.json", {"market_date": "", "latest_valid_node": "", "captured_at": "", "node_status": "NON_TRADING_DAY", "latest_snapshot": "", "snapshot_commit": "", "superseded_nodes": [], "data_freshness": {}, "account_fact": {"status": "MISSING", "updated_at": "", "source": ""}, "needs_account_update": True, "last_trade_event_id": "", "rules_version": "V2.2.15", "generated_at": ""})


def make_event(root: Path, node: str, planned: str, title: str) -> dict:
    captured = f"{REPLAY_DATE}T{planned}:00+08:00"
    current = update_current(root=root, market_date=REPLAY_DATE, node=node, captured_at=captured, latest_snapshot=f"data/market/snapshots/replay/{REPLAY_DATE}_{planned.replace(':', '')}_replay.json", snapshot_commit="REPLAY", node_status="READY", data_freshness={"status": "REPLAY_HISTORICAL_DAILY", "provider": "hithink-finance-history"})
    context = build_decision_context(root)
    atomic_json_write(root / "data" / "state" / "decision_context.json", context)
    action = "需要上传账户截图" if context["account_fact_status"] == "MISSING" else "无需补充账户事实"
    created_at = captured
    body = "【ETF节点提醒】\n时间：" + planned + "\n最新有效节点：" + current["latest_valid_node"] + "\n数据状态：" + current["data_freshness"]["status"] + "\n账户状态：" + context["account_fact_status"] + "\n是否需要处理：" + action
    return {"notification_id": event_id(node, current["latest_valid_node"], created_at), "created_at": created_at, "market_date": REPLAY_DATE, "node": node, "latest_valid_node": current["latest_valid_node"], "title": title, "data_status": current["data_freshness"]["status"], "account_fact_status": context["account_fact_status"], "need_user_action": context["account_fact_status"] == "MISSING", "reason": "账户事实缺失，请上传最新券商截图" if context["account_fact_status"] == "MISSING" else "状态提醒", "context_file": "data/state/decision_context.json", "current_version": "V2.2.15", "body": body, "status": "CREATED"}


def main() -> None:
    source_root = Path(__file__).resolve().parents[1]
    output = source_root / "notifications"
    simulation_output = source_root / "tests" / "validation" / "account_update_simulation"
    (output / "replay").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "notification_workspace"
        setup(root, source_root)
        events = []
        for node, planned, title in NODES:
            events.append(make_event(root, node, planned, title))
        old_notification_current = read_current(root)["latest_valid_node"] == "1130"
        for event in events:
            atomic_json_write(output / "replay" / f"{REPLAY_DATE}_{event['node']}_notification_event.json", event)
        atomic_json_write(output / "notification_event.json", events[-1])
        # Simulate a user-provided screenshot without fabricating account numbers.
        simulation_fact = {"updated_at": "2026-08-21T11:40:00+08:00", "source": "BROKER_SCREENSHOT", "status": "VALID", "simulation_only": True, "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []}
        atomic_json_write(root / "data" / "state" / "account_fact.json", simulation_fact)
        update_current(root=root, market_date=REPLAY_DATE, node="1130", captured_at="2026-08-21T11:30:00+08:00", latest_snapshot="data/market/snapshots/replay/2026-08-21_1130_replay.json", snapshot_commit="REPLAY", node_status="READY", data_freshness={"status": "REPLAY_HISTORICAL_DAILY", "provider": "hithink-finance-history"})
        valid_context = build_decision_context(root)
        atomic_json_write(simulation_output / "account_fact.json", simulation_fact)
        atomic_json_write(simulation_output / "decision_context_after_valid.json", valid_context)
        atomic_json_write(simulation_output / "result.json", {"before": "MISSING", "after": valid_context["account_fact_status"], "simulation_only": True, "formal_amount_generated": False})
        # Separate degraded-data check.
        degraded_root = Path(temp) / "degraded_workspace"
        setup(degraded_root, source_root)
        update_current(root=degraded_root, market_date=REPLAY_DATE, node="1330", captured_at="2026-08-21T13:30:00+08:00", node_status="DEGRADED", data_freshness={"status": "DATA_ERROR"})
        degraded_context = build_decision_context(degraded_root)
        preview = "# ChatGPT 入口上下文模拟\n\n当前有效节点：1130\n\nCURRENT摘要：市场日期 2026-08-21，状态 READY，最新节点 1130。\n\n市场数据状态：REPLAY_HISTORICAL_DAILY。\n\n账户事实状态：" + valid_context["account_fact_status"] + "。\n\n需要用户提供的信息：当前账户事实为模拟 VALID 占位接口；正式使用前仍需提供最新券商截图。\n\n旧通知处理：点击 10:30 通知时读取 CURRENT，当前有效节点为 1130。\n\n异常处理：数据异常时状态为 " + degraded_context["current"]["node_status"] + "，不生成金额判断。\n\n本文件仅用于交互入口模拟，不产生交易建议或订单。\n"
        (source_root / "tests" / "validation" / "chat_context_preview.md").write_text(preview, encoding="utf-8")
        report = {"replay_date": REPLAY_DATE, "notifications_created": len(events), "nodes": [e["node"] for e in events], "old_notification_switched_to_1130": old_notification_current, "account_before": "MISSING", "account_after": valid_context["account_fact_status"], "account_simulation_only": True, "degraded_status": degraded_context["current"]["node_status"], "trade_output_generated": False, "native_push_implemented": False}
        atomic_json_write(output / "simulation_result.json", report)
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
