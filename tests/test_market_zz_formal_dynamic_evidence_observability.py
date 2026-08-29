from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FormalDynamicEvidenceObservabilityTest(unittest.TestCase):
    def test_dynamic_evidence_states_are_explicit_and_auditable(self):
        basis_path = ROOT / "data/state/if_ic_basis_5d_evidence.json"
        oversold_path = ROOT / "data/state/300750_oversold_reversal_evidence.json"
        summary_path = ROOT / "data/state/research_execution_summary.json"
        self.assertTrue(basis_path.exists(), "IF/IC dynamic evidence state must be built before acceptance")
        self.assertTrue(oversold_path.exists(), "300750 dynamic evidence state must be built before acceptance")
        self.assertTrue(summary_path.exists(), "research execution summary must be built before acceptance")

        basis = json.loads(basis_path.read_text(encoding="utf-8"))
        oversold = json.loads(oversold_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        dynamic = summary.get("formal_dynamic_evidence") or {}

        for state in (basis, oversold):
            self.assertIn(state.get("status"), {"READY", "DEGRADED", "BLOCKED"})
            self.assertIsNone(state.get("trade_signal"))
            self.assertFalse(state.get("can_generate_decision_independently"))
            if state.get("status") != "READY":
                self.assertFalse(state.get("use_in_current_decision"))
                self.assertTrue(state.get("reason"))

        self.assertEqual((dynamic.get("if_ic_basis_5d") or {}).get("status"), basis.get("status"))
        self.assertEqual((dynamic.get("ipo_base_stock_oversold_reversal_300750") or {}).get("status"), oversold.get("status"))

        print("FORMAL_DYNAMIC_EVIDENCE=" + json.dumps({
            "if_ic": {
                "status": basis.get("status"),
                "fact_latest_date": basis.get("fact_latest_date"),
                "provider": basis.get("provider"),
                "reason": basis.get("reason"),
            },
            "300750": {
                "status": oversold.get("status"),
                "completed_bar_date": oversold.get("completed_bar_date"),
                "provider": oversold.get("provider"),
                "pattern_match": oversold.get("pattern_match"),
                "reason": oversold.get("reason"),
            },
        }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
