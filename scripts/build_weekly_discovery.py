"""Persist the current pulsed ETF Discovery and the weekly close artifact.

The artifact is a research input only. It never promotes an opportunity, changes
Observation identity, grants trading permission, or creates a trade fact.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

try:
    from formal_etf_opportunity_discovery import discover_formal_candidates
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.formal_etf_opportunity_discovery import discover_formal_candidates
    from scripts.state_manager import atomic_json_write

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CURRENT_OUTPUT = ROOT / "data/state/formal_discovery_current.json"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default



def build() -> dict:
    current = load_json(ROOT / "data/state/CURRENT.json", {})
    market_date = str(current.get("market_date") or "").strip()
    node = str(current.get("latest_valid_node") or "").strip().lower()
    node_status = str(current.get("node_status") or "").strip().upper()
    snapshot = load_json(ROOT / str(current.get("latest_snapshot") or ""), {})
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
    generated = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    artifact = {
        "schema_version": "1.0",
        "layer": "WEEKLY_DISCOVERY",
        "status": str(discovery.get("status") or "DEGRADED").upper(),
        "market_date": market_date,
        "latest_valid_node": "close",
        "generated_at_beijing": generated,
        "source": discovery.get("source"),
        "coverage_status": discovery.get("coverage_status"),
        "candidate_count": discovery.get("candidate_count", 0),
        "universe_identity": discovery.get("universe_identity"),
        "discovery_delta_identity": discovery.get("discovery_delta_identity"),
        "discovery": discovery,
        "read_only": True,
        "decision_owner": "MASTER_FORMAL_DECISION",
    }
    current_artifact = {
        **artifact,
        "layer": "CURRENT_PULSE_DISCOVERY",
        "latest_valid_node": node,
        "latest_snapshot": current.get("latest_snapshot"),
        "formation_rule": "EVERY_VERIFIED_A_SHARE_PULSE",
        "consumption_rule": "Formal Decision and downstream contexts consume the latest same-node legal result; failure is explicit and never converted to zero candidates.",
        "identity_boundary": "This current-pulse artifact is a discovery input only; it does not establish Observation, opportunity, Trial, Confirm, amount, sell quantity or order.",
    }
    atomic_json_write(CURRENT_OUTPUT, current_artifact)

    return {
        "status": current_artifact["status"],
        "market_date": market_date,
        "latest_valid_node": node,
        "candidate_count": current_artifact["candidate_count"],
        "coverage_status": current_artifact["coverage_status"],
        "current_output": str(CURRENT_OUTPUT.relative_to(ROOT)),
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))