import json
from pathlib import Path

from scripts.pulse_history import append_pulse_history


def test_pulse_history_is_idempotent_and_keeps_latest(tmp_path: Path):
    payload = {
        "status": "READY",
        "coverage_status": "PARTIAL",
        "market_date": "2026-11-06",
        "node": "1330",
        "generated_at_beijing": "2026-11-06T13:30:00+08:00",
        "captured_at_beijing": "2026-11-06T13:29:00+08:00",
        "candidates": [{"code": "159217"}],
    }
    first = append_pulse_history(
        tmp_path,
        "DISCOVERY",
        payload,
        path="data/state/formal_discovery_current.json",
        identity="2026-11-06:1330:abc",
    )
    second = append_pulse_history(
        tmp_path,
        "DISCOVERY",
        payload,
        path="data/state/formal_discovery_current.json",
        identity="2026-11-06:1330:abc",
    )
    assert first["record_count"] == 1
    assert second["record_count"] == 1
    stored = json.loads((tmp_path / "data/state/discovery_pulse_index.json").read_text())
    assert stored["latest"]["identity"] == "2026-11-06:1330:abc"
    assert stored["latest"]["candidate_count"] == 1


def test_hyperliquid_pulse_history_uses_same_index_contract(tmp_path: Path):
    payload = {
        "status": "READY",
        "generated_at_beijing": "2026-11-06T14:00:00+08:00",
        "latest_observation_collected_at_utc": "2026-11-06T06:00:00+00:00",
        "observations": [{"symbol": "BTC"}],
    }
    result = append_pulse_history(
        tmp_path,
        "HYPERLIQUID",
        payload,
        path="data/state/overseas_context.json#hyperliquid",
        identity="hl-2026-11-06-1400",
    )
    assert result["record_count"] == 1
    stored = json.loads((tmp_path / "data/state/hyperliquid_pulse_index.json").read_text())
    assert stored["latest"]["observation_count"] == 1
    assert stored["latest"]["formed_at_beijing"].endswith("+08:00")
    assert stored["latest"]["fact_time_beijing"].endswith("+08:00")
    assert stored["latest"]["source_path"].endswith("#hyperliquid")


def test_discovery_history_preserves_reason_evidence(tmp_path):
    payload = {
        "market_date": "2026-11-07",
        "generated_at_beijing": "2026-11-07T17:00:00+08:00",
        "status": "READY",
        "coverage_status": "COMPLETE",
        "candidate_count": 1,
        "candidates": [{
            "code": "520550",
            "name": "港股红利低波ETF招商",
            "surfaced_states": [{"state": "PERSISTENT_TREND", "evidence": {"return_5": 0.03}}],
            "discovery_spot": {"change_pct": 1.2},
        }],
    }
    result = append_pulse_history(
        tmp_path, kind="DISCOVERY", payload=payload, path="formal_discovery_current.json",
        identity="test-discovery-evidence",
    )
    assert result["status"] == "UPDATED"
    stored = json.loads((tmp_path / "data/state/discovery_pulse_index.json").read_text())
    candidate = stored["latest"]["candidates"][0]
    assert candidate["surfaced_states"][0]["evidence"]["return_5"] == 0.03
    assert candidate["discovery_spot"]["change_pct"] == 1.2
