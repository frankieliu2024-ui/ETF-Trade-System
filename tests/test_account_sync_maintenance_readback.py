import json
import runpy
import sys
from pathlib import Path


def test_maintenance_receipt_requires_account_fact_readback(tmp_path, monkeypatch):
    root = tmp_path
    request_path = root / "requests/live_snapshot/broker.json"
    request_path.parent.mkdir(parents=True)
    (root / "data/state").mkdir(parents=True)
    fact = {
        "status": "VALID",
        "updated_at": "2026-10-22T15:00:00+08:00",
        "total_asset": 100.0,
        "stock_market_value": 40.0,
        "cash": 60.0,
        "positions": [{"code": "159941", "quantity": 10}],
    }
    request_path.write_text(json.dumps({"request_id": "broker", "account_fact": fact}), encoding="utf-8")
    (root / "data/state/account_fact.json").write_text(json.dumps(fact), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "record_account_sync_maintenance.py",
        "--root", str(root),
        "--request-id", "broker",
        "--request-path", "requests/live_snapshot/broker.json",
        "--owner-outcome", "success",
        "--run-id", "1",
    ])
    runpy.run_module("scripts.record_account_sync_maintenance", run_name="__main__")
    result = json.loads((root / "data/state/maintenance_health.json").read_text())
    assert result["account_sync"]["status"] == "PASS"
    assert result["account_sync"]["readback_status"] == "CONFIRMED"


def test_maintenance_receipt_rejects_success_without_convergence(tmp_path, monkeypatch):
    root = tmp_path
    request_path = root / "requests/live_snapshot/broker.json"
    request_path.parent.mkdir(parents=True)
    (root / "data/state").mkdir(parents=True)
    request_path.write_text(json.dumps({"request_id": "broker", "account_fact": {
        "updated_at": "2026-10-22T15:00:00+08:00",
        "total_asset": 101.0,
        "stock_market_value": 40.0,
        "cash": 61.0,
        "positions": [],
    }}), encoding="utf-8")
    (root / "data/state/account_fact.json").write_text(json.dumps({
        "updated_at": "2026-10-21T15:00:00+08:00",
        "total_asset": 100.0,
        "stock_market_value": 40.0,
        "cash": 60.0,
        "positions": [],
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "record_account_sync_maintenance.py",
        "--root", str(root),
        "--request-id", "broker",
        "--request-path", "requests/live_snapshot/broker.json",
        "--owner-outcome", "success",
        "--run-id", "1",
    ])
    runpy.run_module("scripts.record_account_sync_maintenance", run_name="__main__")
    result = json.loads((root / "data/state/maintenance_health.json").read_text())
    assert result["account_sync"]["status"] == "OWNER_REJECTED"
    assert result["account_sync"]["failure_class"] == "ACCOUNT_FACT_READBACK_MISMATCH"


def test_market_snapshot_binds_latest_broker_request_for_formal_run():
    workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    assert "newest unconsumed screenshot" in workflow
    assert "ACCOUNT_SYNC_REQUEST_FILE" in workflow
    assert "sync_request_path" in workflow
