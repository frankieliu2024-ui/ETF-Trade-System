from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_phase4_automation as phase4
from scripts import state_manager
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
            (root / "config/market").mkdir(parents=True, exist_ok=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "111111"}, {"code": "222222"}, {"code": "333333"}]}),
                encoding="utf-8",
            )
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

    def test_canonical_account_schema_completes_capital_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "data/market/snapshots/test.json"
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            (root / "config/market").mkdir(parents=True, exist_ok=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [
                    {"code": "561980"}, {"code": "588000"}, {"code": "159941"},
                    {"code": "159781"}, {"code": "159326"}, {"code": "518880"},
                    {"code": "159992"},
                ]}),
                encoding="utf-8",
            )
            snapshot_path.write_text(json.dumps({"rows": [
                {"asset_class": "ETF", "symbol": "561980", "name": "半导体设备ETF", "quality_status": "PASS"},
                {"asset_class": "ETF", "symbol": "159992", "name": "创新药ETF", "quality_status": "PASS"},
            ]}), encoding="utf-8")
            account = {"positions": [
                {"code": "561980", "name": "半导体设备ETF", "quantity": 38900, "current_price": 0.644, "pnl": -7913.30, "holding_pnl": 999.0},
                {"code": "588000", "name": "科创50ETF", "quantity": 14100, "current_price": 1.668, "pnl": -6075.61},
                {"code": "159941", "name": "纳指ETF", "quantity": 12200, "current_price": 1.664, "pnl": 464.20},
                {"code": "159781", "name": "科创创业ETF", "quantity": 19500, "current_price": 1.032, "pnl": -4624.60},
                {"code": "159326", "name": "电网设备ETF", "quantity": 3000, "current_price": 1.643, "pnl": -29.0},
                {"code": "518880", "name": "黄金ETF", "quantity": 500, "current_price": 9.164, "pnl": 22.95},
                {"code": "300750", "name": "宁德时代", "quantity": 100, "current_price": 351.0, "pnl": -4278.07},
                {"code": "601138", "name": "工业富联", "quantity": 500, "current_price": 63.69, "pnl": 2817.78},
                {"code": "301689", "name": "电科思仪", "quantity": 500, "current_price": 16.0, "pnl": 0.0},
            ]}
            old_root = phase4.ROOT
            phase4.ROOT = root
            try:
                ranking = phase4._build_ranking(
                    {"latest_snapshot": "data/market/snapshots/test.json", "captured_at": "2026-09-05T09:30:00+08:00"},
                    account, {"status": "READY", "components": {"risk": {"etf_strategy_risk_pct": -8.1}}},
                    {}, {},
                )
            finally:
                phase4.ROOT = old_root
        rows = ranking["comparison_universe"]
        by_code = {row.get("code"): row for row in rows}
        self.assertEqual(len(rows), len({row.get("code") for row in rows}))
        for code in ("561980", "588000", "159941", "159781", "159326", "518880"):
            self.assertEqual(by_code[code]["category"], "HELD_ETF")
            self.assertEqual(by_code[code]["eligibility"], "HOLDING_COMPARISON")
        for code in ("300750", "601138", "301689"):
            self.assertEqual(by_code[code]["category"], "ACCOUNT_STOCK")
        self.assertEqual(by_code["159992"]["category"], "OBSERVED_ETF")
        self.assertIsNone(ranking["top_candidate"])
        self.assertEqual(ranking["candidate_selection_status"], "REQUIRES_MASTER_DECISION")
        self.assertFalse(any("score" in str(row).lower() for row in rows))

    def test_state_manager_uses_current_pnl_and_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config/market").mkdir(parents=True, exist_ok=True)
            (root / "data/state").mkdir(parents=True, exist_ok=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "561980"}]}), encoding="utf-8"
            )
            (root / "data/state/account_fact.json").write_text(json.dumps({
                "positions": [{"code": "561980", "quantity": 100, "pnl": -12.5, "holding_pnl": 99.0}]
            }), encoding="utf-8")
            (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-09-05"}), encoding="utf-8")
            old_root = state_manager.ROOT
            state_manager.ROOT = root
            try:
                metrics = state_manager.build_etf_strategy_risk_metrics(root)
            finally:
                state_manager.ROOT = old_root
        self.assertAlmostEqual(metrics["etf_holding_unrealized_pct"], -0.00625, places=6)
