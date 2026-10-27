import json
from datetime import datetime, timezone, timedelta


def test_pulse_trigger_creates_only_formal_request(tmp_path, monkeypatch):
    import scripts.create_pulse_formal_trigger as trigger

    root = tmp_path
    state = root / "data/state"
    requests = root / "requests/live_snapshot"
    state.mkdir(parents=True)
    requests.mkdir(parents=True)
    state_file = state / "decision_trigger.json"
    state_file.write_text(json.dumps({
        "requires_formal_reassessment": True,
        "pending_trigger": False,
        "idempotency_key": "pulse-1",
        "trigger_type": "CANDIDATE_CHANGE",
        "evidence_change": "candidate changed",
        "evidence_time": "2026-10-27T10:05:00+08:00",
        "applicable_object": "159992",
    }), encoding="utf-8")
    monkeypatch.setattr(trigger, "ROOT", root)
    monkeypatch.setattr(trigger, "STATE_PATH", state_file)
    monkeypatch.setattr(trigger, "REQUEST_DIR", requests)
    consumed = state / "formal_pulse_trigger_state.json"
    monkeypatch.setattr(trigger, "CONSUMED_PATH", consumed)
    result = trigger.create_request(
        now=datetime(2026, 10, 27, 10, 10, tzinfo=timezone(timedelta(hours=8))),
        task_run_id="run-1",
        occurrence_id="scheduled_pulse_run-1",
    )
    assert result["status"] == "CREATED"
    payload = json.loads(next(requests.glob("*.json")).read_text(encoding="utf-8"))
    assert payload["task_id"] == "ETF正式决策"
    assert payload["trigger_origin"] == "FIXED_MARKET_PULSE"
    assert payload["no_trade_authority"] is True
    assert json.loads(state_file.read_text(encoding="utf-8"))["pending_trigger"] is True
    assert json.loads(consumed.read_text(encoding="utf-8"))["idempotency_key"] == "pulse-1"


def test_pulse_trigger_is_idempotent_when_pending(tmp_path, monkeypatch):
    import scripts.create_pulse_formal_trigger as trigger

    root = tmp_path
    state_file = root / "decision_trigger.json"
    state_file.write_text(json.dumps({
        "requires_formal_reassessment": True,
        "pending_trigger": True,
        "idempotency_key": "pulse-1",
    }), encoding="utf-8")
    monkeypatch.setattr(trigger, "STATE_PATH", state_file)
    monkeypatch.setattr(trigger, "CONSUMED_PATH", tmp_path / "consumed.json")
    assert trigger.create_request(now=datetime.now(timezone.utc), task_run_id="run-2", occurrence_id="scheduled_pulse_run-2")["status"] == "CREATED"
