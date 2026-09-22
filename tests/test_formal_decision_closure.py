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
    def test_parent_without_decision_is_not_complete(self):
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"DECISION_NOT_PERSISTED"); self.assertFalse(x["complete"])
    def test_persisted_decision_without_acceptance_is_not_complete(self):
        (self.root/"events/decisions/d1.json").write_text(json.dumps({"event_type":"FORMAL_DECISION","decision_id":"d1","parent_request_id":self.parent}),encoding="utf-8")
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"PERSISTED_ACCEPTANCE_UNPROVEN"); self.assertFalse(x["complete"])
    def test_wrong_parent_does_not_close_request(self):
        (self.root/"events/decisions/d1.json").write_text(json.dumps({"event_type":"FORMAL_DECISION","decision_id":"d1","parent_request_id":"other"}),encoding="utf-8")
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"DECISION_NOT_PERSISTED")
    def test_duplicate_parent_decisions_fail_closed(self):
        for n in ("d1","d2"):
            (self.root/f"events/decisions/{n}.json").write_text(json.dumps({"event_type":"FORMAL_DECISION","decision_id":n,"parent_request_id":self.parent}),encoding="utf-8")
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"AMBIGUOUS_CANONICAL_DECISION"); self.assertFalse(x["complete"])
    def test_canonical_decision_plus_acceptance_is_complete(self):
        (self.root/"events/decisions/d1.json").write_text(json.dumps({"event_type":"FORMAL_DECISION","decision_id":"d1","parent_request_id":self.parent}),encoding="utf-8")
        (self.root/"data/state/e2e_status.json").write_text(json.dumps({"status":"PASS","acceptance_sha":"abc123"}),encoding="utf-8")
        x=formal_decision_closure(self.root,self.parent)
        self.assertEqual(x["status"],"COMPLETE"); self.assertTrue(x["complete"]); self.assertEqual(x["decision_id"],"d1")
if __name__=="__main__": unittest.main()
