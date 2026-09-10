import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_research_integration",
    ROOT / "scripts" / "check_research_integration.py",
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
