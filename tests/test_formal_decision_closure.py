from __future__ import annotations
import json, tempfile, unittest
from pathlib import Path
from scripts.formal_decision_closure import formal_decision_closure

class FormalDecisionClosureTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name)
        (self.root/"requests/live_snapshot").mkdir(parents=True)
        (self.root/"events/decisions").mkdir(parents=True)
        (self.root/"data/state").mkdir(parents=True)
        self.parent="req_1"
        (self.root/"requests/live_snapshot"/f"{self.parent}.json").write_text(json.dumps({"request_id":self.parent}),encoding="utf-8")
    def tearDown(self): self.t.cleanup()
    def event(self, **overrides):
        value={
            "event_type":"FORMAL_DECISION",
            "decision_id":"d1",
            "request_id":"req_1__formal_completion",
            "parent_request_id":self.parent,
            "fingerprint":"abc123",
            "point_in_time_status":"CONSUMED_SNAPSHOT_VALIDATED",
            "formal_decision":{"decision_id":"d1"},
        }
        value.update(overrides)
        return value
    def write_event(self, value=None, name="d1.json"):
        (self.root/"events/decisions"/name).write_text(json.dumps(value or self.event()),encoding="utf-8")
    def test_parent_without_decision_is_not_complete(self):
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"DECISION_NOT_PERSISTED"); self.assertFalse(x["complete"])
    def test_wrong_parent_does_not_close_request(self):
        self.write_event(self.event(parent_request_id="other"))
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"DECISION_NOT_PERSISTED")
    def test_duplicate_parent_decisions_fail_closed(self):
        self.write_event(self.event(), "d1.json")
        self.write_event(self.event(decision_id="d2", formal_decision={"decision_id":"d2"}), "d2.json")
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"AMBIGUOUS_CANONICAL_DECISION"); self.assertFalse(x["complete"])
    def test_invalid_canonical_readback_fails_closed(self):
        self.write_event(self.event(fingerprint=""))
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"DECISION_POST_WRITE_ACCEPTANCE_FAILED"); self.assertFalse(x["complete"])
    def test_decision_identity_mismatch_fails_closed(self):
        self.write_event(self.event(formal_decision={"decision_id":"other"}))
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"DECISION_POST_WRITE_ACCEPTANCE_FAILED")
    def test_request_bound_query_time_pit_is_accepted(self):
        self.write_event(self.event(point_in_time_status="REQUEST_BOUND_QUERY_TIME_PIT_VALIDATED", request_bound_query_time_pit=True))
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"COMPLETE"); self.assertTrue(x["complete"])
    def test_canonical_decision_readback_closes_without_projection_acceptance(self):
        self.write_event()
        # A stale/failing repository-wide projection must not own Formal Decision closure.
        (self.root/"data/state/e2e_status.json").write_text(json.dumps({"status":"FAIL","acceptance_sha":""}),encoding="utf-8")
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"COMPLETE"); self.assertTrue(x["complete"])
        self.assertEqual(x["decision_id"],"d1")
        self.assertEqual(x["post_write_acceptance"],"CANONICAL_DECISION_FACT_READBACK_VALIDATED")
if __name__=="__main__": unittest.main()
