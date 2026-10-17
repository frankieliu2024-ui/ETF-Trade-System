from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "process_state_sync_request.py").read_text(encoding="utf-8")


def test_business_decision_source_recovers_dwp_after_execution_time_main_reset():
    assert 'request_type == BUSINESS_DECISION_SOURCE' in SOURCE
    assert 'request.get("decision_work_package")' in SOURCE
    assert 'request["_dwp_recovered_from_durable_bds"] = True' in SOURCE
    assert 'recovered_trigger["request_id"] = parent_id' in SOURCE


def test_recovery_is_not_a_general_request_identity_bypass():
    block = SOURCE[SOURCE.index('if str(trigger.get("request_id") or "").strip() != parent_id:'):]
    assert 'request_type == BUSINESS_DECISION_SOURCE' in block
    assert 'else:\n                qualifier = "historical" if replay_source_commit else "current"' in block
