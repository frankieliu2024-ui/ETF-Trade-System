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
    assert stored["latest"]["source_path"].endswith("#hyperliquid")
