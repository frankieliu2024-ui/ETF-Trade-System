import json
from pathlib import Path

import pytest

from scripts.validate_hyperliquid_pulse_persistence import validate


def write_state(root: Path, context_id: str = "ctx-1", *, observations=True, continuity="READY"):
    state = root / "data" / "state"
    state.mkdir(parents=True)
    (state / "overseas_context.json").write_text(
        json.dumps({"hyperliquid": {"context_id": context_id}}), encoding="utf-8"
    )
    (state / "hyperliquid_pulse_index.json").write_text(
        json.dumps({
            "latest": {
                "identity": "hl-1",
                "context_id": context_id,
                "observations": [{"asset": "xyz:XBI"}] if observations else [],
            },
            "continuity": {"status": continuity},
        }),
        encoding="utf-8",
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
