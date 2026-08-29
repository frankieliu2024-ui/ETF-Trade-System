from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_research_execution_bridge import _research_conclusion_digest


ROOT = Path(__file__).resolve().parents[1]
FORMAL_CONCLUSION = ROOT / "research/backtests/index_futures_if_ic_basis_formal_conclusion.json"
MASTER = ROOT / "ETF规则_MASTER.md"
EXPECTED_ETFS = {"561980", "588000", "159781", "159992", "515880", "159326"}


class IfIcBasisFormalRegistrationTest(unittest.TestCase):
    def test_master_and_formal_conclusion_are_aligned(self) -> None:
        master = MASTER.read_text(encoding="utf-8")
        conclusion = json.loads(FORMAL_CONCLUSION.read_text(encoding="utf-8"))

        self.assertIn("IF／IC 5日基差变化证据", master)
        # Section 6.4 is now a concise unified evidence catalog. Preserve the
        # substantive role contract (IF primary, IC supplementary) without
        # coupling this guard to one historical sentence about stability margin.
        self.assertIn("IF为主", master)
        self.assertIn("IC为补充", master)
        self.assertNotIn("股指期货IF／IC／IM基差与OI仍需逐合约、到期日明确的最终验证", master)

        self.assertTrue(conclusion["decision_eligible"])
        self.assertTrue(conclusion["production_context_integration"])
        self.assertTrue(conclusion["use_as_decision_evidence"])
        self.assertFalse(conclusion["can_generate_decision_independently"])
        self.assertFalse(conclusion["automatic_trade"])
        self.assertIsNone(conclusion["trade_signal"])
        self.assertEqual(set(conclusion["applies_to_etf_codes"]), EXPECTED_ETFS)

        signals = {item["signal_id"]: item for item in conclusion["validated_signals"]}
        self.assertEqual(set(signals), {"IF_basis_change_5d", "IC_basis_change_5d"})
        self.assertEqual(signals["IF_basis_change_5d"]["role"], "PRIMARY")
        self.assertEqual(signals["IC_basis_change_5d"]["role"], "SUPPLEMENTARY")
        self.assertTrue(all(item["can_generate_decision_independently"] is False for item in signals.values()))
        self.assertTrue(all(item["trade_signal"] is None for item in signals.values()))

    def test_execution_bridge_auto_discovers_formal_conclusion_without_trade_authority(self) -> None:
        conclusion = json.loads(FORMAL_CONCLUSION.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "research/backtests/index_futures_if_ic_basis_formal_conclusion.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(conclusion, ensure_ascii=False), encoding="utf-8")

            digest = _research_conclusion_digest(root, {})
            items = digest["execution_eligible_backtest_conclusions"]
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertTrue(item["decision_eligible"])
            self.assertTrue(item["production_context_integration"])
            self.assertTrue(item["use_as_decision_evidence"])
            self.assertFalse(item["can_generate_decision_independently"])
            self.assertIsNone(item["trade_signal"])
            self.assertFalse(digest["automatic_promotion"])
            self.assertIsNone(digest["trade_signal"])


if __name__ == "__main__":
    unittest.main()
