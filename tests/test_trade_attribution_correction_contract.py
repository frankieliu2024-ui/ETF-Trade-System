import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts import apply_trade_fact_correction as correction
from scripts import decision_trade_link as link

class AttributionCorrectionTests(unittest.TestCase):
 def fixture(self,root):
  (root/"events/trades").mkdir(parents=True);(root/"events/decisions").mkdir(parents=True);(root/"requests/trade_fact_correction").mkdir(parents=True)
  d={"decision_id":"decision-a","decision_time_beijing":"2026-09-30T09:36:13+08:00","candidate_code":"513520","formal_decision":{"amount_action":"日经ETF（513520）Trial；买入2,100份"}}
  t={"event_id":"trade-a","execution_status":"EXECUTED","code":"513520","name":"日经ETF","side":"BUY","quantity":2100,"price":2.31,"confirmed_at_beijing":"2026-09-30T09:44:15+08:00","linked_decision_id":None}
  (root/"events/decisions/decision-a.json").write_text(json.dumps(d),encoding="utf-8");p=root/"events/trades/trade-a.json";p.write_text(json.dumps(t,sort_keys=True),encoding="utf-8");return p
 def req(self,rid="attr-1",did="decision-a"):
  return {"request_id":rid,"trade_event_id":"trade-a","linked_decision_id":did,"provenance":{"resolver":"decision_trade_link.resolve_link","source_trade":"events/trades/trade-a.json","source_decision":f"events/decisions/{did}.json","parent_request_id":"manual-formal-20260930-093510"}}
 def test_idempotent_and_immutable(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=self.fixture(root);before=p.read_bytes()
   with patch.object(correction,"ROOT",root): correction._apply_attribution_request(self.req());correction._apply_attribution_request(self.req())
   self.assertEqual(before,p.read_bytes());self.assertEqual(link.resolve_link(root,json.loads(p.read_text()))[0],"decision-a")
 def test_temporal_and_conflict_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=self.fixture(root);bad=json.loads(p.read_text());bad["confirmed_at_beijing"]="2026-09-30T09:30:00+08:00";p.write_text(json.dumps(bad))
   with patch.object(correction,"ROOT",root): self.assertRaises(RuntimeError,correction._apply_attribution_request,self.req())
   p.write_text(json.dumps(json.loads((root/"events/trades/trade-a.json").read_text())|{"confirmed_at_beijing":"2026-09-30T09:44:15+08:00"}))
if __name__=="__main__":unittest.main()
