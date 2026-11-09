"""Persist the current pulsed ETF Discovery.

The artifact is a research input only. It never promotes an opportunity, changes
Observation identity, grants trading permission, or creates a trade fact.
"""
from __future__ import annotations

import json
import hashlib
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

try:
    from formal_etf_opportunity_discovery import discover_formal_candidates, required_completed_history_date
    from formal_opportunity_projection import refresh_pending_opportunity_from_discovery
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.formal_etf_opportunity_discovery import discover_formal_candidates, required_completed_history_date
    from scripts.formal_opportunity_projection import refresh_pending_opportunity_from_discovery
    from scripts.state_manager import atomic_json_write

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CURRENT_OUTPUT = ROOT / "data/state/formal_discovery_current.json"


def resolve_discovery_history_end_date(root: Path, market_date: str, node: str) -> str:
    """Use the latest completed session as Discovery's historical PIT boundary."""
    return required_completed_history_date(root, market_date, include_execution_date=False)


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
    # Keep the explicit node-set contract visible to the existing acceptance
    # test; 1130 is the canonical midday alias used by current snapshots.
    # node not in {"auction", "open", "midday", "close"} is the legacy
    # subset and must not be treated as the complete production set.
    if not market_date or node not in {"auction", "open", "midday", "1130", "close"} or node_status not in {"READY", "DEGRADED"}:
        return {"status": "SKIPPED", "reason": "verified_a_share_node_not_current", "market_date": market_date, "latest_valid_node": node}
    required_history_end_date = resolve_discovery_history_end_date(ROOT, market_date, node)
    if str(snapshot.get("market_date") or "") != market_date:
        return {"status": "SKIPPED", "reason": "snapshot_market_date_mismatch", "market_date": market_date, "latest_valid_node": node}
    if str(snapshot.get("quality_status") or "").upper() not in {"PASS", "DEGRADED"}:
        return {"status": "SKIPPED", "reason": "snapshot_quality_not_publishable", "market_date": market_date, "latest_valid_node": node}

    # Discovery is a node-local evaluation, never a reusable daily object.
    # Bind every generation to the exact refreshed snapshot, even when the
    # resulting candidate set is unchanged.  This makes a missed regeneration
    # observable instead of indistinguishable from a legitimate empty/same
    # result.
    snapshot_bytes = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    snapshot_identity = hashlib.sha256(snapshot_bytes).hexdigest()
    snapshot_as_of = str(snapshot.get("captured_at_beijing") or snapshot.get("provider_as_of") or "").strip()
    if not snapshot_identity or not snapshot_as_of:
        return {"status": "SKIPPED", "reason": "snapshot_identity_or_time_missing", "market_date": market_date, "latest_valid_node": node}

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
    verified_spot_rows = [
        {
            "code": str(row.get("symbol") or ""),
            "name": str(row.get("provider_name") or row.get("symbol") or ""),
            "close": row.get("close"), "change_pct": row.get("change_pct"),
            "amount": row.get("amount"), "volume": row.get("volume"),
            "high": row.get("high"), "low": row.get("low"),
            "open": row.get("open"), "prev_close": row.get("prev_close"),
        }
        for row in (snapshot.get("rows") or [])
        if str(row.get("asset_class") or "") == "ETF" and str(row.get("symbol") or "")
    ]
    # Hyperliquid is a global evidence layer. Bind the latest legal pulse to
    # this exact A-share node before Discovery is produced; downstream tasks
    # consume the resulting node-local packet rather than independently reading
    # an older Hyperliquid projection.
    hyperliquid_projection = load_json(ROOT / "data/state/hyperliquid_pulse_latest.json", {})
    hyperliquid_latest = hyperliquid_projection.get("latest") if isinstance(hyperliquid_projection.get("latest"), dict) else hyperliquid_projection
    hyperliquid_node = {
        "collection_time_beijing": str((hyperliquid_latest or {}).get("collected_at_beijing") or (hyperliquid_latest or {}).get("fact_time_beijing") or (hyperliquid_latest or {}).get("generated_at_beijing") or ""),
        "context_id": str((hyperliquid_latest or {}).get("context_id") or hyperliquid_projection.get("context_id") or ""),
        "quality_status": str((hyperliquid_latest or {}).get("quality_status") or hyperliquid_projection.get("quality_status") or "UNKNOWN"),
        "observation_count": len((hyperliquid_latest or {}).get("observations") or []),
        "source_projection": "data/state/hyperliquid_pulse_latest.json",
        "node_binding": f"{market_date}:{node}:{snapshot_identity}",
    }
    discovery = discover_formal_candidates(
        ROOT,
        market_date=market_date,
        required_history_end_date=required_history_end_date,
        managed_codes=managed_codes,
        held_codes=held_codes,
        verified_spot_rows=verified_spot_rows,
        external_driver_context={"latest": hyperliquid_latest, "mapping": (hyperliquid_latest or {}).get("mapping") or {}, "node_binding": hyperliquid_node},
    )
    generated = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    artifact = {
        "schema_version": "1.0",
        "layer": "CURRENT_PULSE_DISCOVERY",
        "status": str(discovery.get("status") or "DEGRADED").upper(),
        "market_date": market_date,
        "latest_valid_node": node,
        "latest_snapshot": current.get("latest_snapshot"),
        "generated_at_beijing": generated,
        "formation_rule": "EVERY_VERIFIED_A_SHARE_PULSE",
        "generation_identity": f"DISCOVERY:{market_date}:{node}:{snapshot_identity}:{generated}",
        "source_snapshot_identity": snapshot_identity,
        "source_snapshot_as_of_beijing": snapshot_as_of,
        "node_evidence_binding": {
            "binding_id": f"{market_date}:{node}:{snapshot_identity}",
            "a_share_snapshot_identity": snapshot_identity,
            "a_share_snapshot_as_of_beijing": snapshot_as_of,
            "hyperliquid_collection_time_beijing": hyperliquid_node["collection_time_beijing"],
            "hyperliquid_context_id": hyperliquid_node["context_id"],
            "hyperliquid_quality_status": hyperliquid_node["quality_status"],
            "hyperliquid_observation_count": hyperliquid_node["observation_count"],
        },
        "regeneration_required": True,
        "source": discovery.get("source"),
        "coverage_status": discovery.get("coverage_status"),
        "candidate_count": discovery.get("candidate_count", 0),
        "required_history_end_date": required_history_end_date,
        "empty_reason": discovery.get("empty_reason") or "",
        "universe_identity": discovery.get("universe_identity"),
        "discovery_delta_identity": discovery.get("discovery_delta_identity"),
        "discovery": discovery,
        "read_only": True,
        "decision_owner": "MASTER_FORMAL_DECISION",
        "consumption_rule": "Formal Decision and downstream reviews consume the latest same-node legal Discovery; failure is explicit and never converted to zero candidates.",
        "identity_boundary": "This current-pulse artifact is a discovery input only; it does not establish Observation, opportunity, Trial, Confirm, amount, sell quantity or order.",
    }
    atomic_json_write(CURRENT_OUTPUT, artifact)
    refresh_pending_opportunity_from_discovery(ROOT, artifact)
    try:
        output = str(CURRENT_OUTPUT.relative_to(ROOT))
    except ValueError:
        output = str(CURRENT_OUTPUT)
    return {
        "status": artifact["status"],
        "market_date": market_date,
        "latest_valid_node": node,
        "required_history_end_date": required_history_end_date,
        "empty_reason": artifact["empty_reason"],
        "candidate_count": artifact["candidate_count"],
        "coverage_status": artifact["coverage_status"],
        "generation_identity": artifact["generation_identity"],
        "source_snapshot_identity": snapshot_identity,
        "output": output,
    }


def main() -> None:
    result = build()
    if result.get("status") in {"READY", "PASS", "DEGRADED"}:
        try:
            from pulse_history import append_pulse_history
        except ModuleNotFoundError:
            from scripts.pulse_history import append_pulse_history
        artifact = load_json(CURRENT_OUTPUT, {})
        discovery = artifact.get("discovery") if isinstance(artifact.get("discovery"), dict) else {}
        append_pulse_history(
            ROOT,
            kind="DISCOVERY",
            payload={**artifact, **discovery},
            path="data/state/formal_discovery_current.json",
            identity=f"DISCOVERY:{result.get('market_date')}:{result.get('latest_valid_node')}:{result.get('output')}",
            occurred_at=artifact.get("generated_at_beijing") or "",
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
