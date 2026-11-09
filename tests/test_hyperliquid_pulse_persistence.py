import json
from pathlib import Path

import pytest

from scripts.validate_hyperliquid_pulse_persistence import validate


def write_state(root: Path, context_id: str = "ctx-1", *, observations=True, continuity="READY", history=None):
    state = root / "data" / "state"
    state.mkdir(parents=True)
    (state / "overseas_context.json").write_text(
        json.dumps({"hyperliquid": {"context_id": context_id}}), encoding="utf-8"
    )
    latest_observations = [{"asset": "xyz:XBI"}] if observations else []
    payload = {
        "latest": {
            "identity": "hl-1",
            "context_id": context_id,
            "formed_at_beijing": "2026-11-09T21:17:05+08:00",
            "observations": latest_observations,
            "etf_bindings": [],
            "binding_candidates": [],
            "binding_status": "LIVE_SIGNAL_MATCHED",
        },
        "continuity": {"status": continuity},
    }
    if history is not None:
        payload["days"] = {"2026-11-09": history}
    (state / "hyperliquid_pulse_index.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_persisted_hyperliquid_pulse_is_consumable(tmp_path: Path):
    write_state(tmp_path)
    assert validate(tmp_path)["status"] == "PASS"


@pytest.mark.parametrize("observations,continuity", [(False, "READY"), (True, "MISSING")])
def test_persistence_gate_rejects_incomplete_index(tmp_path: Path, observations, continuity):
    write_state(tmp_path, observations=observations, continuity=continuity)
    with pytest.raises(RuntimeError):
        validate(tmp_path)


def test_persistence_gate_rejects_context_mismatch(tmp_path: Path):
    write_state(tmp_path)
    index = tmp_path / "data/state/hyperliquid_pulse_index.json"
    payload = json.loads(index.read_text(encoding="utf-8"))
    payload["latest"]["context_id"] = "different"
    index.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError):
        validate(tmp_path)


def test_second_pulse_requires_comparable_delta(tmp_path: Path):
    history = [
        {"identity": "hl-0", "formed_at_beijing": "2026-11-09T21:07:05+08:00"},
        {"identity": "hl-1", "formed_at_beijing": "2026-11-09T21:17:05+08:00"},
    ]
    write_state(tmp_path, history=history)
    with pytest.raises(RuntimeError, match="COMPARABLE"):
        validate(tmp_path)


def test_second_pulse_accepts_real_comparison(tmp_path: Path):
    history = [
        {"identity": "hl-0", "formed_at_beijing": "2026-11-09T21:07:05+08:00"},
        {"identity": "hl-1", "formed_at_beijing": "2026-11-09T21:17:05+08:00"},
    ]
    state = tmp_path / "data/state/hyperliquid_pulse_index.json"
    write_state(
        tmp_path,
        history=history,
    )
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["latest"]["observations"] = [{
        "asset": "xyz:XBI",
        "pulse_comparison_status": "COMPARABLE",
        "previous_pulse_as_of": "2026-11-09T21:07:04+08:00",
        "comparison_horizon": "INTRADAY_10M",
        "pulse_delta": {"open_interest": 1.25},
    }]
    state.write_text(json.dumps(payload), encoding="utf-8")
    assert validate(tmp_path)["history_record_count"] == 2
