from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_561980_component_lead_evidence as mod


class ComponentLead561980Test(unittest.TestCase):
    def make_root(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "research/backtests").mkdir(parents=True)
        (root / "config/market").mkdir(parents=True)
        (root / "data/state").mkdir(parents=True)
        (root / "research/backtests/561980_component_lead_formal_conclusion.json").write_text(
            json.dumps({
                "decision_eligible": True,
                "production_context_integration": True,
                "reviewed_current_top5": {"codes": ["002371", "300604", "300666", "300346", "002409"]},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (root / "config/market/a_share_trading_calendar_2026.json").write_text(
            json.dumps({"closed_dates": []}), encoding="utf-8"
        )
        return root

    def test_pcf_crypto_runtime_capability(self):
        params, headers = mod._encrypted_pcf_params({
            "startDate": "2026-08-31",
            "productCode": "561980",
            "pageNum": 1,
            "pageSize": 1000,
            "isPreview": "0",
        })
        self.assertEqual(params["encrypted"], "true")
        self.assertEqual(params["request_id"], mod.ENVELOPE_REQUEST_ID)
        self.assertGreater(len(params["data"]), 50)
        self.assertEqual(len(headers["tk-trans-signature"]), 64)
        self.assertEqual(headers["tk-trans-merchant-key"], "thinkive")

    def test_pcf_crypto_missing_openssl_is_explicit(self):
        with mock.patch.object(mod.subprocess, "run", side_effect=FileNotFoundError("openssl")):
            with self.assertRaisesRegex(RuntimeError, "openssl binary unavailable"):
                mod._encrypted_pcf_params({"productCode": "561980"})

    def test_ready_signal_uses_dynamic_pcf_top5_and_d_minus_1_prices(self):
        root = self.make_root()
        pcf = [
            {"code": "002371", "name": "A", "td_amount": 500.0},
            {"code": "300604", "name": "B", "td_amount": 400.0},
            {"code": "300666", "name": "C", "td_amount": 300.0},
            {"code": "300346", "name": "D", "td_amount": 200.0},
            {"code": "002409", "name": "E", "td_amount": 100.0},
        ] + [
            {"code": f"000{i:03d}"[-6:], "name": "X", "td_amount": 1.0}
            for i in range(30)
        ]
        dates = ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]
        prices = {
            "002371": [100, 102, 104, 106],
            "300604": [100, 101, 102, 103],
            "300666": [100, 100, 101, 102],
            "300346": [100, 99, 100, 101],
            "002409": [100, 100, 100, 100],
            "561980": [100, 101, 102, 102],
        }

        def fake_price(code, start, end):
            return dict(zip(dates, prices[code]))

        with mock.patch.object(mod, "_decision_and_cutoff_dates", return_value=("2026-08-31", "2026-08-28")), \
             mock.patch.object(mod, "_fetch_pcf", return_value=pcf), \
             mock.patch.object(mod, "_fetch_daily_closes", side_effect=fake_price):
            out = mod.build(root)

        self.assertEqual(out["status"], "READY")
        self.assertTrue(out["use_in_current_decision"])
        self.assertEqual(out["decision_market_date"], "2026-08-31")
        self.assertEqual(out["price_cutoff_market_date"], "2026-08-28")
        self.assertEqual([x["code"] for x in out["top5"]], ["002371", "300604", "300666", "300346", "002409"])
        self.assertFalse(out["can_generate_decision_independently"])
        self.assertIsNone(out["trade_signal"])
        self.assertAlmostEqual(out["leader_minus_etf_3d_lag1_pct_points"], 0.4, places=4)

    def test_pcf_failure_degrades_without_reusing_old_direction(self):
        root = self.make_root()
        with mock.patch.object(mod, "_decision_and_cutoff_dates", return_value=("2026-08-31", "2026-08-28")), \
             mock.patch.object(mod, "_fetch_pcf", side_effect=RuntimeError("pcf down")):
            out = mod.build(root)
        self.assertEqual(out["status"], "DEGRADED")
        self.assertFalse(out["use_in_current_decision"])
        self.assertNotIn("leader_minus_etf_3d_lag1_pct_points", out)
        self.assertFalse(out["can_generate_decision_independently"])
        self.assertIsNone(out["trade_signal"])

    def test_formal_registration_and_bridge_contract(self):
        formal = json.loads((ROOT / "research/backtests/561980_component_lead_formal_conclusion.json").read_text(encoding="utf-8"))
        bridge = (ROOT / "scripts/build_research_execution_bridge.py").read_text(encoding="utf-8")
        self.assertTrue(formal["decision_eligible"])
        self.assertTrue(formal["production_context_integration"])
        self.assertFalse(formal["can_generate_decision_independently"])
        self.assertIsNone(formal["trade_signal"])
        self.assertEqual(formal["scope"], ["561980"])
        for token in [
            "build_561980_component_lead_evidence",
            "component_lead_561980_3d",
            "561980_component_lead_evidence.json",
            "etf_object_evidence",
        ]:
            self.assertIn(token, bridge)


if __name__ == "__main__":
    unittest.main()
