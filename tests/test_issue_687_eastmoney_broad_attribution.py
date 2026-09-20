import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("probe",ROOT/"scripts"/"issue_687_eastmoney_broad_attribution.py")
MOD=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)

class Issue687EastmoneyBroadAttributionTests(unittest.TestCase):
    def test_probe_is_bounded_and_does_not_guess_mk_buckets(self):
        self.assertEqual(set(MOD.SELECTORS), {"SH_ALL","SZ_ALL"})
        self.assertEqual(MOD.SELECTORS["SH_ALL"], "m:1+t:2,m:1+t:23")
        self.assertEqual(MOD.SELECTORS["SZ_ALL"], "m:0+t:6,m:0+t:80")
        self.assertIn("MK0021", MOD.CURRENT_FS)
        self.assertNotIn("b:MK", ",".join(MOD.SELECTORS.values()))

    def test_probe_only_reads_query_context_and_writes_requested_artifact(self):
        source=(ROOT/"scripts"/"issue_687_eastmoney_broad_attribution.py").read_text(encoding="utf-8")
        self.assertIn('Path("data/state/query_context.json").read_text',source)
        self.assertNotIn("write_text(", source.split("def main():",1)[0])
        self.assertIn("Path(a.output).write_text",source)

    def test_current_fs_failure_does_not_destroy_broad_attribution(self):
        def fake_fetch(fs):
            if fs == MOD.CURRENT_FS:
                raise RuntimeError("502")
            if fs == MOD.SELECTORS["SH_ALL"]:
                return [{"f12":"511010"}]
            raise RuntimeError("reset")
        with patch.object(MOD,"fetch",side_effect=fake_fetch):
            out=MOD.build_attribution({"511010","551001"})
        self.assertEqual(out["current_fs_probe"]["status"],"UNAVAILABLE")
        self.assertEqual(out["selectors"]["SH_ALL"]["status"],"READY")
        self.assertEqual(out["evidence_status"],"PARTIAL")
        self.assertEqual(out["conclusion_status"],"ATTRIBUTABLE")
        self.assertEqual(out["broader_union_target_hits"],["511010"])
        self.assertIn("CURRENT_FS",out["probe_errors"])
        self.assertIn("SZ_ALL",out["probe_errors"])

    def test_all_broad_failures_are_inconclusive_not_missing(self):
        with patch.object(MOD,"fetch",side_effect=RuntimeError("provider unavailable")):
            out=MOD.build_attribution({"511010"})
        self.assertEqual(out["evidence_status"],"UNAVAILABLE")
        self.assertEqual(out["conclusion_status"],"INCONCLUSIVE")
        self.assertEqual(out["missing_after_broader_union"],[])
        self.assertEqual(out["successful_broad_selectors"],[])

if __name__=="__main__": unittest.main()
