"""Run Discovery and Hyperliquid through independent, read-only shadow entries.

This is an acceptance bridge only. It must not publish canonical state, emit a
notification, or replace either existing producer.  The two engines are
invoked independently and their result is written to stdout for CI/artifact
inspection.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(root: Path) -> dict:
    import scripts.build_weekly_discovery as discovery
    from scripts.hyperliquid_external_context import build as build_hyperliquid

    with tempfile.TemporaryDirectory(prefix="etf-engine-shadow-") as temp:
        shadow_output = Path(temp) / "formal_discovery_current.json"
        original_root = discovery.ROOT
        original_output = discovery.CURRENT_OUTPUT
        try:
            discovery.ROOT = root
            discovery.CURRENT_OUTPUT = shadow_output
            discovery_result = discovery.build()
        finally:
            discovery.ROOT = original_root
            discovery.CURRENT_OUTPUT = original_output

        hyperliquid_result = build_hyperliquid()

    return {
        "schema_version": "1.0",
        "mode": "INDEPENDENT_ENGINE_SHADOW",
        "read_only": True,
        "notification_emitted": False,
        "canonical_state_published": False,
        "discovery": discovery_result,
        "hyperliquid": {
            "status": hyperliquid_result.get("quality_status", "MISSING"),
            "context_id": hyperliquid_result.get("context_id", ""),
            "observation_count": hyperliquid_result.get("observation_count", 0),
            "focused_count": hyperliquid_result.get("focused_count", 0),
            "dynamic_active_count": hyperliquid_result.get("dynamic_active_count", 0),
            "missing_focused_assets": hyperliquid_result.get("missing_focused_assets", []),
            "collection_started_at_utc": hyperliquid_result.get("collection_started_at_utc", ""),
            "collection_finished_at_utc": hyperliquid_result.get("collection_finished_at_utc", ""),
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))
