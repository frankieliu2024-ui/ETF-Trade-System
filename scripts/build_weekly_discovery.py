"""Persist the weekly ETF Discovery at the last legal A-share close.

The artifact is a research input only. It never promotes an opportunity, changes
Observation identity, grants trading permission, or creates a trade fact.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from formal_etf_opportunity_discovery import discover_formal_candidates
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.formal_etf_opportunity_discovery import discover_formal_candidates
    from scripts.state_manager import atomic_json_write

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CALENDAR = ROOT / "config/market/a_share_trading_calendar_2026.json"
OUTPUT = ROOT / "data/state/weekly_formal_discovery.json"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def is_last_legal_day_of_week(market_date: str, closed_dates: set[str]) -> bool:
    day = date.fromisoformat(market_date)
    for offset in range(1, 7 - day.weekday()):
        candidate = day + timedelta(days=offset)
        if candidate.weekday() < 5 and candidate.isoformat() not in closed_dates:
            return False
    return True


def build() -> dict:
    current = load_json(ROOT / "data/state/CURRENT.json", {})
    market_date = str(current.get("market_date") or "").strip()
    node = str(current.get("latest_valid_node") or "").strip().lower()
    node_status = str(current.get("node_status") or "").strip().upper()
    snapshot = load_json(ROOT / str(current.get("latest_snapshot") or ""), {})
    calendar = load_json(CALENDAR, {})
    closed_dates = set(calendar.get("closed_dates") or [])

    if not market_date or node != "close" or node_status not in {"READY", "DEGRADED"}:
        return {"status": "SKIPPED", "reason": "verified_close_not_current", "market_date": market_date, "latest_valid_node": node}
    if not is_last_legal_day_of_week(market_date, closed_dates):
        return {"status": "SKIPPED", "reason": "not_last_legal_day_of_week", "market_date": market_date}
    if str(snapshot.get("market_date") or "") != market_date:
        return {"status": "SKIPPED", "reason": "close_snapshot_market_date_mismatch", "market_date": market_date}
    prior = load_json(OUTPUT, {})
    if str(prior.get("market_date") or "") == market_date and str(prior.get("week_end") or "") == market_date and str(prior.get("status") or "").upper() in {"READY", "DEGRADED"}:
        return {"status": "ALREADY_FORMED", "market_date": market_date, "candidate_count": prior.get("candidate_count"), "output": str(OUTPUT.relative_to(ROOT))}
    if str(snapshot.get("quality_status") or "").upper() not in {"PASS", "DEGRADED"}:
        return {"status": "SKIPPED", "reason": "close_snapshot_quality_not_publishable", "market_date": market_date}

    universe = load_json(ROOT / "config/market/etf_monitor_universe.json", {})
    account = load_json(ROOT / "data/state/account_fact.json", {})
    managed_codes = {
        str(item.get("code") or "").strip()
        for item in (universe.get("objects") or [])
        if item.get("code")
    }
    managed_codes.update(
        str(item.get("code") or item.get("symbol") or "").strip()
        for item in (account.get("positions") or [])
        if item.get("code") or item.get("symbol")
    )
    held_codes = {
        str(item.get("code") or item.get("symbol") or "").strip()
        for item in (account.get("positions") or [])
        if str(item.get("asset_type") or "").upper() == "ETF" and int(item.get("quantity") or 0) > 0
    }
    monday = date.fromisoformat(market_date) - timedelta(days=date.fromisoformat(market_date).weekday())
    discovery = discover_formal_candidates(
        ROOT,
        market_date=market_date,
        required_history_end_date=market_date,
        managed_codes=managed_codes,
        held_codes=held_codes,
    )
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    artifact = {
        "schema_version": "1.0",
        "layer": "WEEKLY_DISCOVERY",
        "status": str(discovery.get("status") or "DEGRADED").upper(),
        "market_date": market_date,
        "week_start": monday.isoformat(),
        "week_end": market_date,
        "latest_valid_node": "close",
        "generated_at_beijing": generated,
        "formation_rule": "LAST_LEGAL_A_SHARE_TRADING_DAY_AFTER_VERIFIED_CLOSE",
        "source": discovery.get("source"),
        "coverage_status": discovery.get("coverage_status"),
        "candidate_count": discovery.get("candidate_count", 0),
        "universe_identity": discovery.get("universe_identity"),
        "discovery_delta_identity": discovery.get("discovery_delta_identity"),
        "discovery": discovery,
        "read_only": True,
        "decision_owner": "MASTER_FORMAL_DECISION",
        "consumption_rule": "周日周复盘只读取本文件；不得在周日临时发起全市场Discovery。文件缺失或不可用必须显式报告，不得解释为候选为零。",
        "identity_boundary": "本文件是周级发现输入，不自动建立Observation、机会ETF、CASE、交易许可或交易事实。",
    }
    atomic_json_write(OUTPUT, artifact)
    return {
        "status": artifact["status"],
        "market_date": market_date,
        "candidate_count": artifact["candidate_count"],
        "coverage_status": artifact["coverage_status"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
