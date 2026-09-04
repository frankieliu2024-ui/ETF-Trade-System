from __future__ import annotations
import json,tempfile,unittest
from pathlib import Path
from scripts.run_production_acceptance import is_complete_consistency_report,persist_consistency_report_if_valid
def report(status="PASS"): return {"status":status,"hard_error_count":0,"warning_count":0,"checks":[],"errors":[],"warnings":[]}
class ProductionAcceptanceConsistencyPersistenceTests(unittest.TestCase):
 def test_invalid_report_preserves_existing(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/"s.json"; old=report("WARNING"); p.write_text(json.dumps(old),encoding="utf-8"); self.assertFalse(persist_consistency_report_if_valid({},p)); self.assertEqual(json.loads(p.read_text()),old)
 def test_invalid_and_incomplete_rejected(self):
  self.assertFalse(is_complete_consistency_report({})); self.assertFalse(is_complete_consistency_report(None)); self.assertFalse(is_complete_consistency_report({"status":"PASS","hard_error_count":0}))
 def test_pass_warning_fail_complete(self):
  for status in ("PASS","WARNING","FAIL"): self.assertTrue(is_complete_consistency_report(report(status)))
 def test_valid_replaces_state(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/"s.json"; p.write_text("{}",encoding="utf-8"); fresh=report(); self.assertTrue(persist_consistency_report_if_valid(fresh,p)); self.assertEqual(json.loads(p.read_text()),fresh)
if __name__=="__main__": unittest.main()
