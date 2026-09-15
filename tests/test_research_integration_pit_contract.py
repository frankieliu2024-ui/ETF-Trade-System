import importlib.util
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "check_research_integration",
    SCRIPTS / "check_research_integration.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


VALID = "\n".join([
    "def _snapshot_is_valid_for_decision",
    "market_fact_cutoff",
    "availability_cutoff",
    "SNAPSHOT_MARKET_FACT_AFTER_DECISION_CUTOFF",
    "SNAPSHOT_NOT_AVAILABLE_BY_PERSISTENCE_BOUNDARY",
    "def select_point_in_time_snapshot",
    "consumed_snapshot",
    "CONSUMED_SNAPSHOT_REFERENCE_INVALID",
    "CONSUMED_SNAPSHOT_VALIDATED",
    "POINT_IN_TIME_SNAPSHOT_TWO_CLOCK_VALIDATED",
    "NO_PRIOR_SNAPSHOT",
    "cutoff = parse_time(decision_time)",
    "supplied_time <= cutoff",
    "build_comparison_snapshot(snapshot)",
])


def test_current_pit_contract_passes_without_legacy_captured_cutoff_token():
    assert "captured <= cutoff" not in VALID
    assert MODULE._pit_contract_present(VALID)


def test_missing_market_fact_cutoff_fail_safe_contract_fails():
    broken = VALID.replace("SNAPSHOT_MARKET_FACT_AFTER_DECISION_CUTOFF", "REMOVED")
    assert not MODULE._pit_contract_present(broken)


def test_missing_consumed_snapshot_validation_contract_fails():
    broken = VALID.replace("CONSUMED_SNAPSHOT_VALIDATED", "REMOVED")
    assert not MODULE._pit_contract_present(broken)


def test_missing_decision_cutoff_binding_fails():
    broken = VALID.replace("cutoff = parse_time(decision_time)", "REMOVED")
    assert not MODULE._pit_contract_present(broken)


import json
import tempfile
from unittest import mock

from scripts import check_research_integration
from scripts import build_research_contribution_audit as contribution_audit
from scripts import build_research_master_feedback as master_feedback
from scripts import build_state_context


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _research_root() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory()


def test_canonical_outcome_blocks_intraday_daily_feature_as_close():
    with _research_root() as td:
        root = Path(td)
        _write_json(root / "config/market/etf_monitor_universe.json", {"objects": [{"code": "561980", "name": "半导体设备ETF"}]})
        _write_json(root / "events/decisions/D1.json", {
            "decision_id": "D1",
            "market_date": "2026-09-13",
            "candidate_code": "561980",
            "price_at_decision": 1.0,
            "formal_decision": {"amount_action": "买入561980", "research_evidence_used": []},
        })
        _write_json(root / "data/market/snapshots/2026-09-14_1413.json", {
            "market_date": "2026-09-14",
            "node": "live",
            "planned_time": "",
            "market_phase": "CONTINUOUS_AFTERNOON",
            "quality_status": "PASS",
            "rows": [{
                "code": "561980",
                "quality_status": "PASS",
                "open": 1.0,
                "high": 1.12,
                "low": 0.99,
                "close": 1.1,
                "volume": 100,
                "amount": 1000,
                "as_of_beijing": "2026-09-14T14:13:41+08:00",
            }],
        })
        _write_json(root / "events/research/daily_features/2026-09-14.json", {
            "market_date": "2026-09-14",
            "market_phase": "CONTINUOUS_AFTERNOON",
            "as_of_beijing": "2026-09-14T14:13:41+08:00",
            "source_snapshot": "data/market/snapshots/2026-09-14_1413.json",
            "features": [{"code": "561980", "close": 1.1}],
        })
        result = contribution_audit.build(root)
        outcome = json.loads((root / "events/research/decision_outcomes/D1.json").read_text(encoding="utf-8"))
        assert result["outcome_files"]["schema_version"] == "2.0"
        assert outcome["horizons"]["T_plus_1"]["status"] == "PENDING"
        assert outcome["horizons"]["T_plus_1"]["maturity_blocked_by"] == "QUALIFIED_CLOSE_SEQUENCE_INCOMPLETE"
        assert outcome["horizons"]["T_plus_1"]["blocked_dates"][0]["reason"] == "UNVERIFIED_SESSION_CLOSE:UNVERIFIED"


def test_canonical_outcome_matures_explicit_verified_1500_session_close():
    with _research_root() as td:
        root = Path(td)
        _write_json(root / "config/market/etf_monitor_universe.json", {"objects": [{"code": "561980", "name": "半导体设备ETF"}]})
        _write_json(root / "events/decisions/D1.json", {
            "decision_id": "D1",
            "market_date": "2026-09-13",
            "candidate_code": "561980",
            "price_at_decision": 1.0,
            "formal_decision": {"amount_action": "买入561980", "research_evidence_used": [{"evidence_id": "E1", "decision_effect": "changed size"}]},
        })
        _write_json(root / "data/market/snapshots/2026-09-14_1500.json", {
            "market_date": "2026-09-14",
            "node": "close",
            "planned_time": "15:00",
            "market_phase": "POST_CLOSE_GRACE",
            "quality_status": "PASS",
            "rows": [{
                "code": "561980",
                "quality_status": "PASS",
                "open": 1.0,
                "high": 1.12,
                "low": 0.99,
                "close": 1.1,
                "volume": 100,
                "amount": 1000,
                "as_of_beijing": "2026-09-14T15:03:00+08:00",
            }],
        })
        _write_json(root / "events/research/daily_features/2026-09-14.json", {
            "market_date": "2026-09-14",
            "market_phase": "POST_CLOSE_GRACE",
            "source_snapshot": "data/market/snapshots/2026-09-14_1500.json",
            "features": [{"code": "561980", "close": 1.1}],
        })
        contribution_audit.build(root)
        outcome = json.loads((root / "events/research/decision_outcomes/D1.json").read_text(encoding="utf-8"))
        assert outcome["schema_version"] == "2.0"
        assert outcome["horizons"]["T_plus_1"]["status"] == "MATURED"
        assert outcome["horizons"]["T_plus_1"]["close_qualification"] == "VERIFIED_SESSION_CLOSE"
        assert outcome["horizons"]["T_plus_1"]["candidate_close_return_pct"] == 10.0


def test_master_feedback_counts_schema2_matured_horizons_only():
    with _research_root() as td:
        root = Path(td)
        _write_json(root / "events/decisions/D1.json", {"decision_id": "D1"})
        _write_json(root / "events/research/decision_outcomes/schema1.json", {
            "decision_id": "schema1",
            "results": {"T_plus_1_close_return_pct": 1.0},
        })
        _write_json(root / "events/research/decision_outcomes/schema2.json", {
            "decision_id": "schema2",
            "horizons": {
                "T_plus_1": {"status": "MATURED"},
                "T_plus_3": {"status": "PENDING"},
                "T_plus_5": {"status": "DATA_MISSING"},
            },
        })
        result = master_feedback.build(root)
        assert result["current_inventory"]["outcomes_with_T_plus_1"] == 1
        assert result["current_inventory"]["outcomes_with_T_plus_3"] == 0
        assert result["current_inventory"]["outcomes_with_T_plus_5"] == 0


def test_state_context_does_not_run_retrospective_research_maintenance_fast_path():
    source = (ROOT / "scripts" / "build_state_context.py").read_text(encoding="utf-8")
    assert "from build_research_contribution_audit import" not in source
    assert "from build_research_master_feedback import" not in source
    assert "slow-path/on-demand" in source
    assert "build_research_execution_bridge" in source
    assert "build_research_evidence_delta" in source


def test_research_integration_checker_executes_successfully_on_repository():
    with tempfile.TemporaryDirectory() as td:
        os.environ["ETF_CONSISTENCY_REPORT_PATH"] = str(Path(td) / "system_consistency.json")
        check_research_integration.REPORT = Path(os.environ["ETF_CONSISTENCY_REPORT_PATH"]).resolve()
        assert check_research_integration.main() == 0


def test_live_post_close_grace_without_close_node_remains_pending():
    with _research_root() as td:
        root = Path(td)
        _write_json(root / "config/market/etf_monitor_universe.json", {"objects": [{"code": "561980", "name": "半导体设备ETF"}]})
        _write_json(root / "events/decisions/D1.json", {
            "decision_id": "D1",
            "market_date": "2026-09-13",
            "candidate_code": "561980",
            "price_at_decision": 1.0,
            "formal_decision": {"amount_action": "买入561980", "research_evidence_used": []},
        })
        _write_json(root / "data/market/snapshots/live.json", {
            "market_date": "2026-09-14",
            "node": "live",
            "planned_time": "",
            "market_phase": "POST_CLOSE_GRACE",
            "quality_status": "PASS",
            "rows": [{"code": "561980", "quality_status": "PASS", "open": 1.0, "high": 1.1, "low": 1.0, "close": 1.05, "volume": 100, "amount": 1000}],
        })
        _write_json(root / "events/research/daily_features/2026-09-14.json", {
            "market_date": "2026-09-14",
            "market_phase": "POST_CLOSE_GRACE",
            "source_snapshot": "data/market/snapshots/live.json",
            "features": [{"code": "561980", "close": 1.05}],
        })
        contribution_audit.build(root)
        outcome = json.loads((root / "events/research/decision_outcomes/D1.json").read_text(encoding="utf-8"))
        assert outcome["horizons"]["T_plus_1"]["status"] == "PENDING"
        assert outcome["horizons"]["T_plus_1"]["maturity_blocked_by"] == "QUALIFIED_CLOSE_SEQUENCE_INCOMPLETE"


def test_missing_close_proof_remains_pending():
    with _research_root() as td:
        root = Path(td)
        _write_json(root / "config/market/etf_monitor_universe.json", {"objects": [{"code": "561980", "name": "半导体设备ETF"}]})
        _write_json(root / "events/decisions/D1.json", {"decision_id": "D1", "market_date": "2026-09-13", "candidate_code": "561980", "price_at_decision": 1.0, "formal_decision": {"amount_action": "买入561980", "research_evidence_used": []}})
        _write_json(root / "events/research/daily_features/2026-09-14.json", {"market_date": "2026-09-14", "market_phase": "POST_CLOSE_GRACE", "features": [{"code": "561980", "close": 1.1}]})
        contribution_audit.build(root)
        outcome = json.loads((root / "events/research/decision_outcomes/D1.json").read_text(encoding="utf-8"))
        assert outcome["horizons"]["T_plus_1"]["status"] == "PENDING"
        assert outcome["horizons"]["T_plus_1"]["blocked_dates"][0]["reason"] == "MISSING_SOURCE_SNAPSHOT"


def test_degraded_or_incomplete_close_remains_pending():
    with _research_root() as td:
        root = Path(td)
        _write_json(root / "config/market/etf_monitor_universe.json", {"objects": [{"code": "561980", "name": "半导体设备ETF"}]})
        _write_json(root / "events/decisions/D1.json", {"decision_id": "D1", "market_date": "2026-09-13", "candidate_code": "561980", "price_at_decision": 1.0, "formal_decision": {"amount_action": "买入561980", "research_evidence_used": []}})
        _write_json(root / "data/market/snapshots/bad_close.json", {
            "market_date": "2026-09-14",
            "node": "close",
            "planned_time": "15:00",
            "market_phase": "POST_CLOSE_GRACE",
            "quality_status": "DEGRADED",
            "rows": [{"code": "561980", "quality_status": "DEGRADED", "open": 1.0, "high": 1.1, "low": 1.0, "close": 1.05, "volume": None, "amount": 1000}],
        })
        _write_json(root / "events/research/daily_features/2026-09-14.json", {
            "market_date": "2026-09-14",
            "market_phase": "POST_CLOSE_GRACE",
            "source_snapshot": "data/market/snapshots/bad_close.json",
            "features": [{"code": "561980", "close": 1.05}],
        })
        contribution_audit.build(root)
        outcome = json.loads((root / "events/research/decision_outcomes/D1.json").read_text(encoding="utf-8"))
        assert outcome["horizons"]["T_plus_1"]["status"] == "PENDING"


def test_t_plus_horizons_use_qualified_closes_after_decision_day_only():
    with _research_root() as td:
        root = Path(td)
        _write_json(root / "config/market/etf_monitor_universe.json", {"objects": [{"code": "561980", "name": "半导体设备ETF"}]})
        _write_json(root / "events/decisions/D1.json", {"decision_id": "D1", "market_date": "2026-09-13", "candidate_code": "561980", "price_at_decision": 1.0, "formal_decision": {"amount_action": "买入561980", "research_evidence_used": []}})
        for date, close in [("2026-09-13", 9.9), ("2026-09-14", 1.1), ("2026-09-15", 1.2), ("2026-09-16", 1.3)]:
            _write_json(root / f"data/market/snapshots/{date}_1500.json", {
                "market_date": date, "node": "close", "planned_time": "15:00", "market_phase": "POST_CLOSE_GRACE", "quality_status": "PASS",
                "rows": [{"code": "561980", "quality_status": "PASS", "open": 1.0, "high": close, "low": 1.0, "close": close, "volume": 100, "amount": 1000}],
            })
            _write_json(root / f"events/research/daily_features/{date}.json", {
                "market_date": date, "market_phase": "POST_CLOSE_GRACE", "source_snapshot": f"data/market/snapshots/{date}_1500.json", "features": [{"code": "561980", "close": close}],
            })
        contribution_audit.build(root)
        outcome = json.loads((root / "events/research/decision_outcomes/D1.json").read_text(encoding="utf-8"))
        assert outcome["horizons"]["T_plus_1"]["market_date"] == "2026-09-14"
        assert outcome["horizons"]["T_plus_3"]["market_date"] == "2026-09-16"
        assert outcome["horizons"]["T_plus_1"]["candidate_close_return_pct"] == 10.0
