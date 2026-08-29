from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_phase4_automation as phase4
from scripts.build_query_context import account_gate_status


class Phase4NoMechanicalHardGatesTest(unittest.TestCase):
    def test_minus_10_is_review_boundary_not_fourth_risk_state(self) -> None:
        self.assertEqual(phase4._risk_zone(-10.5), "RISK_CONTROL")
        self.assertTrue(phase4._enhanced_risk_review(-10.5))
        self.assertNotEqual(phase4._risk_zone(-10.5), "RISK_CONTROL_REVIEW")

    def test_minus_8_boundary_changes_review_context_not_execution_permission(self) -> None:
        above = phase4._risk_zone(-7.99)
        below = phase4._risk_zone(-8.01)
        self.assertEqual(above, "RISK_OBSERVATION")
        self.assertEqual(below, "RISK_CONTROL")
        self.assertEqual(phase4._execution_constraints("READY"), [])
        self.assertIn("MASTER_MINUS_5_RISK_REVIEW_BOUNDARY", phase4._risk_review_context(above, False))
        self.assertIn("MASTER_MINUS_8_RISK_REVIEW_BOUNDARY", phase4._risk_review_context(below, False))
        self.assertNotIn("MASTER_RISK_OBSERVATION_PERMISSION_APPLIES", phase4._execution_constraints("READY"))
        self.assertNotIn("MASTER_RISK_CONTROL_PERMISSION_APPLIES", phase4._execution_constraints("READY"))

    def test_risk_control_and_e2e_blocked_do_not_delete_comparison_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "data/market/snapshots/test.json"
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {"asset_class": "ETF", "symbol": "111111", "name": "持仓ETF", "quality_status": "PASS"},
                            {"asset_class": "ETF", "symbol": "222222", "name": "观察ETF", "quality_status": "PASS"},
                            {"asset_class": "ETF", "symbol": "333333", "name": "失败ETF", "quality_status": "FAILED"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            old_root = phase4.ROOT
            phase4.ROOT = root
            try:
                current = {"latest_snapshot": "data/market/snapshots/test.json", "captured_at": "2026-08-28T10:00:00+08:00"}
                account = {
                    "positions": [
                        {"asset_type": "ETF", "code": "111111", "name": "持仓ETF", "quantity": 1000, "market_value": 10000},
                        {"asset_type": "STOCK", "code": "300750", "name": "宁德时代", "quantity": 100, "market_value": 40000},
                    ]
                }
                e2e = {"status": "BLOCKED", "components": {"risk": {"etf_strategy_risk_pct": -10.2}}}
                ranking = phase4._build_ranking(current, account, e2e, {}, {})
            finally:
                phase4.ROOT = old_root

        self.assertEqual(ranking["risk_zone"], "RISK_CONTROL")
        self.assertEqual(ranking["risk_zone_semantics"], "REVIEW_CONTEXT_ONLY_NOT_TRADING_PERMISSION")
        self.assertTrue(ranking["enhanced_risk_review_required"])
        self.assertIsNone(ranking["top_candidate"])
        self.assertEqual(ranking["candidate_selection_status"], "REQUIRES_MASTER_DECISION")
        self.assertEqual(ranking["excluded_candidates"], [])
        by_code = {row.get("code"): row for row in ranking["comparison_universe"]}
        self.assertIn("111111", by_code)
        self.assertIn("222222", by_code)
        self.assertIn("333333", by_code)
        self.assertIn("300750", by_code)
        self.assertEqual(by_code["333333"]["data_availability"], "UNAVAILABLE")
        self.assertEqual(by_code["222222"]["category"], "OBSERVED_ETF")
        self.assertIn("E2E_BLOCKED_FORMAL_AMOUNT_OR_SHARE_DECISION_REQUIRES_MISSING_CRITICAL_FACT", by_code["222222"]["execution_constraints"])
        self.assertIn("MASTER_MINUS_8_RISK_REVIEW_BOUNDARY", by_code["222222"]["risk_review_context"])
        self.assertNotIn("MASTER_RISK_CONTROL_PERMISSION_APPLIES", by_code["222222"]["execution_constraints"])

    def test_risk_boundary_crossing_only_requests_reassessment(self) -> None:
        current = {"captured_at": "2026-08-28T10:00:00+08:00"}
        account = {"positions": [], "formal_action": {}}
        e2e = {"status": "READY", "components": {"risk": {"etf_strategy_risk_pct": -8.01}}}
        prior = {"risk_zone": "RISK_OBSERVATION", "pending_trigger": False}
        trigger = phase4._build_trigger(current, account, e2e, {}, prior, {}, {})
        self.assertEqual(trigger["trigger_type"], "RISK_BOUNDARY_CROSSED")
        self.assertTrue(trigger["requires_formal_reassessment"])
        self.assertEqual(trigger["risk_zone_semantics"], "REVIEW_CONTEXT_ONLY_NOT_TRADING_PERMISSION")
        self.assertIn("formal permission must be re-decided by MASTER", trigger["evidence_change"])

    def test_event_driven_account_carry_forward_is_default(self) -> None:
        current = {"market_date": "2026-08-28"}
        account = {"status": "VALID", "updated_at": "2026-08-27T16:01:00+08:00"}
        gate = account_gate_status(current, account, {})
        self.assertFalse(gate["same_market_date_required"])
        self.assertTrue(gate["can_use_current_account_fact"])
        self.assertFalse(gate["requires_user_broker_screenshot"])


if __name__ == "__main__":
    unittest.main()
