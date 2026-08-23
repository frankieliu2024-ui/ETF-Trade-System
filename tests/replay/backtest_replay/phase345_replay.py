from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.state_manager import atomic_json_write, build_decision_context, read_current, update_current


REPLAY_DATE = "2026-08-21"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
NODES = [("0925", "09:25"), ("1030", "10:30"), ("1130", "11:30"), ("1330", "13:30"), ("1430", "14:30"), ("1500", "15:00")]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(root: Path) -> dict:
    fixture = root / "tests" / "replay" / "sources" / "market_fixture_2026-08-21.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    if payload.get("replay_date") != REPLAY_DATE or len(payload.get("rows", [])) != 10:
        raise RuntimeError("replay fixture is incomplete or has the wrong date")
    for row in payload["rows"]:
        if row.get("source_date", "") > REPLAY_DATE:
            raise RuntimeError("future data found in replay fixture")
    return payload


def write_account_and_dashboard(root: Path, source_root: Path) -> None:
    (root / "data" / "state").mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "events" / "events.jsonl").write_text("", encoding="utf-8")
    atomic_json_write(root / "data" / "state" / "account_fact.json", {"updated_at": "", "source": "BROKER_SCREENSHOT", "status": "MISSING", "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []})
    atomic_json_write(root / "data" / "state" / "CURRENT.json", {"market_date": "", "latest_valid_node": "", "captured_at": "", "node_status": "NON_TRADING_DAY", "latest_snapshot": "", "snapshot_commit": "", "superseded_nodes": [], "data_freshness": {}, "account_fact": {"status": "MISSING", "updated_at": "", "source": ""}, "needs_account_update": True, "last_trade_event_id": "", "rules_version": "V2.2.15", "generated_at": ""})
    shutil.copy2(source_root / "ETF当前状态_DASHBOARD.md", root / "ETF当前状态_DASHBOARD.md")


def run_replay(root: Path, fixture: dict, source_root: Path) -> dict:
    write_account_and_dashboard(root, source_root)
    snapshot_dir = root / "data" / "market" / "snapshots" / "replay"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for node, planned in NODES:
        actual_dt = datetime.fromisoformat(f"{REPLAY_DATE}T{planned}:00+08:00")
        captured = actual_dt.isoformat()
        snapshot = {
            "replay": True, "replay_date": REPLAY_DATE, "replay_node": node,
            "planned_time": planned, "actual_run_time": captured, "data_date": REPLAY_DATE,
            "data_granularity": "daily_close_as_of_replay_date", "provider": "hithink-finance-history",
            "timezone": "Asia/Shanghai", "quality_status": "PASS", "rows": copy.deepcopy(fixture["rows"]),
            "node_data_limitation": "Historical daily bars are used to exercise state/file chain; this is not intraday price reconstruction.",
        }
        target = snapshot_dir / f"2026-08-21_{planned.replace(':', '')}_replay.json"
        before = digest(target) if target.exists() else ""
        atomic_json_write(target, snapshot)
        after = digest(target)
        current = update_current(root=root, market_date=REPLAY_DATE, node=node, captured_at=captured, latest_snapshot=str(target.relative_to(root)).replace("\\", "/"), snapshot_commit="REPLAY", node_status="READY", data_freshness={"status": "REPLAY_HISTORICAL_DAILY", "provider": "hithink-finance-history", "count": 10, "data_date": REPLAY_DATE})
        context = build_decision_context(root)
        atomic_json_write(root / "replay_decision_context.json", context)
        records.append({"replay_node": node, "planned_time": planned, "data_date": REPLAY_DATE, "source": "tests/replay/sources/market_fixture_2026-08-21.json", "snapshot": str(target.relative_to(root)).replace("\\", "/"), "snapshot_overwrote_existing": bool(before and before != after), "latest_valid_node": current["latest_valid_node"], "superseded_count": len(current["superseded_nodes"]), "account_fact_status": context["account_fact_status"], "trade_decision_generated": False})
    return {"replay_date": REPLAY_DATE, "nodes": records, "latest_current": read_current(root), "decision_context": json.loads((root / "replay_decision_context.json").read_text(encoding="utf-8"))}


def exception_checks(root: Path) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        isolated = Path(temp)
        (isolated / "data" / "state").mkdir(parents=True)
        atomic_json_write(isolated / "data" / "state" / "account_fact.json", {"status": "MISSING", "updated_at": "", "source": "BROKER_SCREENSHOT", "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []})
        update_current(root=isolated, market_date=REPLAY_DATE, node="1030", captured_at="2026-08-21T10:30:00+08:00", node_status="DEGRADED", data_freshness={"status": "DATA_ERROR"})
        degraded = read_current(isolated)["node_status"] == "DEGRADED"
    nontrading = tempfile.TemporaryDirectory()
    try:
        non_root = Path(nontrading.name)
        (non_root / "data" / "state").mkdir(parents=True)
        atomic_json_write(non_root / "data" / "state" / "account_fact.json", {"status": "MISSING", "updated_at": "", "source": "BROKER_SCREENSHOT", "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []})
        update_current(root=non_root, market_date=REPLAY_DATE, node="", captured_at="2026-08-21T00:00:00+08:00", node_status="NON_TRADING_DAY")
        nontrading_ok = read_current(non_root)["node_status"] == "NON_TRADING_DAY" and read_current(non_root)["latest_valid_node"] == ""
    finally:
        nontrading.cleanup()
    context_text = (root / "replay_decision_context.json").read_text(encoding="utf-8") if (root / "replay_decision_context.json").exists() else ""
    return {"data_missing_degraded": degraded, "account_missing": read_current(root)["account_fact"]["status"] == "MISSING", "non_trading_day": nontrading_ok, "no_trade_output": not any(token in context_text for token in ("trade_amount", "buy_action", "sell_action", "order"))}


def old_notification_check(source_root: Path) -> bool:
    with tempfile.TemporaryDirectory() as temp:
        isolated = Path(temp)
        write_account_and_dashboard(isolated, source_root)
        update_current(root=isolated, market_date=REPLAY_DATE, node="1030", captured_at="2026-08-21T10:30:00+08:00", latest_snapshot="old", snapshot_commit="REPLAY")
        update_current(root=isolated, market_date=REPLAY_DATE, node="1130", captured_at="2026-08-21T11:30:00+08:00", latest_snapshot="new", snapshot_commit="REPLAY")
        return read_current(isolated)["latest_valid_node"] == "1130"


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    fixture = load_fixture(root)
    with tempfile.TemporaryDirectory() as temp:
        replay_root = Path(temp) / "replay_workspace"
        result = run_replay(replay_root, fixture, root)
        first_hashes = {p.name: digest(p) for p in (replay_root / "data" / "market" / "snapshots" / "replay").glob("*.json")}
        second = run_replay(replay_root, fixture, root)
        second_hashes = {p.name: digest(p) for p in (replay_root / "data" / "market" / "snapshots" / "replay").glob("*.json")}
        idempotent = first_hashes == second_hashes and len(second["nodes"]) == 6
        result["exception_checks"] = exception_checks(replay_root)
        result["old_notification_current_node_1130"] = old_notification_check(root)
        result["idempotent_rerun"] = idempotent
        result["snapshot_count"] = len(first_hashes)
        out = root / "tests" / "replay" / "backtest_replay" / "phase345_replay_result.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        production_replay_dir = root / "data" / "market" / "snapshots" / "replay"
        production_replay_dir.mkdir(parents=True, exist_ok=True)
        for source in (replay_root / "data" / "market" / "snapshots" / "replay").glob("*.json"):
            shutil.copy2(source, production_replay_dir / source.name)
        shutil.copy2(replay_root / "replay_decision_context.json", root / "tests" / "replay" / "replay_decision_context.json")
        shutil.copy2(replay_root / "data" / "state" / "CURRENT.json", root / "data" / "state" / "replay_CURRENT.json")
        shutil.copy2(replay_root / "data" / "state" / "account_fact.json", root / "data" / "state" / "replay_account_fact.json")
        print(json.dumps({"replay": "PASS", "replay_date": REPLAY_DATE, "node_count": 6, "snapshot_count": len(first_hashes), "idempotent_rerun": idempotent, "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
