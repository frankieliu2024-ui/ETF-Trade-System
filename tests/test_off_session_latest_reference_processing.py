from __future__ import annotations

import json
from pathlib import Path

from scripts.runtime_session_gate import latest_legal_close_reference, should_process_latest_reference


def _fixture(tmp_path: Path, *, market_date: str = "2026-11-06", quality: str = "PASS") -> tuple[Path, dict]:
    root = tmp_path
    (root / "data/state").mkdir(parents=True)
    (root / "data/market/snapshots").mkdir(parents=True)
    snapshot_rel = "data/market/snapshots/close.json"
    snapshot = {
        "market_date": market_date,
        "node": "close",
        "quality_status": quality,
        "rows": [{"symbol": "159992.SZ", "as_of_beijing": f"{market_date}T15:00:00+08:00"}],
    }
    (root / snapshot_rel).write_text(json.dumps(snapshot), encoding="utf-8")
    current = {
        "market_date": market_date,
        "latest_valid_node": "close",
        "node_status": "READY" if quality == "PASS" else "DEGRADED",
        "latest_snapshot": snapshot_rel,
    }
    (root / "data/state/CURRENT.json").write_text(json.dumps(current), encoding="utf-8")
    calendar = {"closed_dates": ["2026-11-09"]}
    return root, calendar


def test_latest_legal_close_is_available_on_weekend_without_fabricating_new_market_date(tmp_path: Path) -> None:
    root, calendar = _fixture(tmp_path)
    out = latest_legal_close_reference(root, "2026-11-07", calendar)
    assert out["available"] is True
    assert out["market_date"] == "2026-11-06"
    assert out["snapshot"] == "data/market/snapshots/close.json"


def test_latest_legal_close_is_available_on_exchange_holiday(tmp_path: Path) -> None:
    root, calendar = _fixture(tmp_path, market_date="2026-11-06")
    out = latest_legal_close_reference(root, "2026-11-09", calendar)
    assert out["available"] is True


def test_latest_close_rejects_intraday_current_or_nonpublishable_snapshot(tmp_path: Path) -> None:
    root, calendar = _fixture(tmp_path)
    current_path = root / "data/state/CURRENT.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["latest_valid_node"] = "live"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    assert latest_legal_close_reference(root, "2026-11-07", calendar)["available"] is False

    current["latest_valid_node"] = "close"
    current["node_status"] = "FAILED"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    assert latest_legal_close_reference(root, "2026-11-07", calendar)["available"] is False


def test_latest_close_rejects_missing_or_mismatched_snapshot(tmp_path: Path) -> None:
    root, calendar = _fixture(tmp_path)
    current_path = root / "data/state/CURRENT.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["latest_snapshot"] = "data/market/snapshots/missing.json"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    assert latest_legal_close_reference(root, "2026-11-07", calendar)["available"] is False


def test_latest_close_reference_workflow_returns_reference_without_new_capture() -> None:
    workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    assert "process_latest_reference" in workflow
    assert "steps.session_gate.outputs.process_latest_reference == 'true'" in workflow
    assert "Resolve latest legal close reference" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "new_snapshot_written" in workflow
    assert "current_mutated" in workflow
    assert "build_query_context.py --run-discovery" not in workflow
    assert "No new live/close snapshot is fabricated" in workflow
    assert 'reason = "latest_legal_close_reference"' in Path("scripts/runtime_session_gate.py").read_text(encoding="utf-8")


def test_latest_reference_processing_is_manual_only_and_respects_probe_and_state_sync_boundaries() -> None:
    available = {"available": True}
    assert should_process_latest_reference("workflow_dispatch", "NOT_APPLICABLE", False, False, available)
    assert not should_process_latest_reference("workflow_dispatch", "NOT_APPLICABLE", False, True, available)
    assert not should_process_latest_reference("workflow_dispatch", "NOT_APPLICABLE", False, False, available, has_replay_request=True)
    assert not should_process_latest_reference("workflow_dispatch", "STATE_SYNC_ONLY", False, False, available)
    assert not should_process_latest_reference("workflow_dispatch", "BUSINESS_DECISION_SOURCE", False, False, available)
    assert not should_process_latest_reference("schedule", "NOT_APPLICABLE", False, False, available)
    assert not should_process_latest_reference("workflow_dispatch", "NOT_APPLICABLE", True, False, available)
    assert not should_process_latest_reference("workflow_dispatch", "NOT_APPLICABLE", False, False, {"available": False})
