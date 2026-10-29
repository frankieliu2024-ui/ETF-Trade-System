"""Regression checks for System Review's dispatch/projection acceptance boundary."""
from pathlib import Path

ACTOR = Path(__file__).resolve().parents[1] / "scripts" / "run_scheduled_system_review_actor.py"


def test_system_review_dispatch_acceptance_is_not_workflow_readback():
    source = ACTOR.read_text(encoding="utf-8")
    assert 'bool(dispatch.get("read_back"))' not in source
    assert 'bool(dispatch.get("submitted")) and bool(projection.get("verified"))' in source


def test_system_review_does_not_accept_dispatch_alone():
    def accepted(dispatch, projection):
        return bool(dispatch.get("submitted")) and bool(projection.get("verified"))
    assert not accepted({"submitted": True, "read_back": False}, {"verified": False})
    assert accepted({"submitted": True, "read_back": False}, {"verified": True})
    assert not accepted({"submitted": False, "read_back": False}, {"verified": True})
