"""Canonical refresh of the current Opportunity projection from the latest Discovery.

A new verified Discovery invalidates the prior pending opportunity input. This
refresh never promotes a candidate, grants Trial/Confirm, or creates a trade.
The formal decision/state-sync producer may later replace this pending projection
with a DECIDED projection using the same source Discovery.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def refresh_pending_opportunity_from_discovery(root: Path = ROOT, discovery: dict | None = None) -> dict:
    discovery = discovery if isinstance(discovery, dict) else _load(root / "data/state/formal_discovery_current.json", {})
    market_date = str(discovery.get("market_date") or "").strip()
    node = str(discovery.get("latest_valid_node") or "").strip().lower()
    snapshot = str(discovery.get("latest_snapshot") or "").strip()
    generated = str(discovery.get("generated_at_beijing") or "").strip()
    if not market_date or not node or not snapshot or not generated:
        return {"status": "SKIPPED", "reason": "discovery_binding_incomplete"}
    path = root / "data/state/formal_opportunity_current.json"
    existing = _load(path, {}) if path.exists() else {}
    # A pulse is a new input boundary. Clear only the current decision projection;
    # historical decision artifacts remain untouched and formal decision remains
    # the sole owner of DECIDED output.
    opportunity = {
        "schema_version": "1.0",
        "layer": "OPPORTUNITY",
        "producer": "formal_opportunity_projection.py",
        "read_only": True,
        "market_date": market_date,
        "latest_valid_node": node,
        "latest_snapshot": snapshot,
        "generated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "status": "PENDING_MASTER_DECISION",
        "opportunity_count": None,
        "opportunities": [],
        "decision_owner": "MASTER_FORMAL_DECISION",
        "not_zero_rule": "PENDING_MASTER_DECISION is not an opportunity count and must not be interpreted as zero",
        "source_discovery_generated_at_beijing": generated,
        "source_discovery_latest_snapshot": snapshot,
        "source_discovery_universe_identity": discovery.get("universe_identity"),
        "source_discovery_delta_identity": discovery.get("discovery_delta_identity"),
        "refresh_reason": "VERIFIED_DISCOVERY_PULSE",
        "previous_projection_status": existing.get("status") if isinstance(existing, dict) else "",
    }
    atomic_json_write(path, opportunity)
    return {"status": "REFRESHED", "market_date": market_date, "latest_valid_node": node, "source_discovery_generated_at_beijing": generated, "source_discovery_latest_snapshot": snapshot}
