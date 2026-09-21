import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_state_context as state_context


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_same_node_discovery_is_reused(tmp_path):
    current = {
        "market_date": "2026-09-21",
        "latest_valid_node": "live",
        "latest_snapshot": "data/market/snapshots/2026-09-21_095150.json",
    }
    discovery = {"status": "DEGRADED", "coverage_status": "PARTIAL", "candidates": [{"code": "159303"}]}
    write_json(tmp_path / "data/state/query_context.json", {
        "market_date": "2026-09-21",
        "latest_valid_node": "live",
        "current": current,
        "formal_etf_discovery": discovery,
    })

    assert state_context.load_node_local_formal_discovery(tmp_path, current) == discovery


def test_stale_snapshot_discovery_is_not_reused(tmp_path):
    current = {
        "market_date": "2026-09-21",
        "latest_valid_node": "live",
        "latest_snapshot": "data/market/snapshots/2026-09-21_100000.json",
    }
    write_json(tmp_path / "data/state/query_context.json", {
        "market_date": "2026-09-21",
        "latest_valid_node": "live",
        "current": {
            **current,
            "latest_snapshot": "data/market/snapshots/2026-09-21_095150.json",
        },
        "formal_etf_discovery": {"status": "READY", "candidates": [{"code": "159303"}]},
    })

    assert state_context.load_node_local_formal_discovery(tmp_path, current) is None


def test_not_requested_discovery_is_not_promoted(tmp_path):
    current = {"market_date": "2026-09-21", "latest_valid_node": "live"}
    write_json(tmp_path / "data/state/query_context.json", {
        "market_date": "2026-09-21",
        "latest_valid_node": "live",
        "formal_etf_discovery": {"status": "NOT_REQUESTED", "candidates": []},
    })

    assert state_context.load_node_local_formal_discovery(tmp_path, current) is None
