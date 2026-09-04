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

 def test_consistency_handoff_prefers_fresh_report_over_stale_canonical(self):
  with tempfile.TemporaryDirectory() as tmp:
   import os
   from scripts import maintenance_guard
   fresh = Path(tmp) / "fresh.json"
   canonical = Path(tmp) / "canonical.json"
   fresh_report = report("WARNING")
   stale_report = report("FAIL")
   fresh.write_text(json.dumps(fresh_report), encoding="utf-8")
   canonical.write_text(json.dumps(stale_report), encoding="utf-8")
   previous = os.environ.get("ETF_CONSISTENCY_REPORT_PATH")
   os.environ["ETF_CONSISTENCY_REPORT_PATH"] = str(fresh)
   try:
    self.assertEqual(maintenance_guard.consistency_path(), fresh)
    self.assertEqual(maintenance_guard.read_json(maintenance_guard.consistency_path()), fresh_report)
    self.assertNotEqual(maintenance_guard.read_json(maintenance_guard.consistency_path()), json.loads(canonical.read_text()))
   finally:
    if previous is None:
     os.environ.pop("ETF_CONSISTENCY_REPORT_PATH", None)
    else:
     os.environ["ETF_CONSISTENCY_REPORT_PATH"] = previous

 def test_invalid_report_preserves_last_known_canonical_state(self):
  with tempfile.TemporaryDirectory() as tmp:
   p = Path(tmp) / "s.json"
   old = report("WARNING")
   p.write_text(json.dumps(old), encoding="utf-8")
   self.assertFalse(persist_consistency_report_if_valid({"status": "WARNING"}, p))
   self.assertEqual(json.loads(p.read_text()), old)

if __name__=="__main__": unittest.main()
