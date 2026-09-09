from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import runtime_self_heal  # noqa: E402


class RuntimeRecoveryConsistencyGateTests(unittest.TestCase):
    def test_known_historical_case_mapping_fail_remains_global_fail_but_not_market_blocker(self):
        gate = runtime_self_heal.consistency_market_recovery_gate({
            "status": "FAIL",
            "errors": ["historical_trade_case_mapping:2026-09-09 13:30:21:515220:case_count=0"],
        })
        self.assertEqual(gate["global_status"], "FAIL")
        self.assertFalse(gate["blocks_market_recovery"])
        self.assertEqual(gate["classification"], "GLOBAL_FAIL_KNOWN_NONBLOCKING_FOR_MARKET_RECOVERY")

    def test_unknown_hard_fail_blocks_market_recovery_fail_safe(self):
        gate = runtime_self_heal.consistency_market_recovery_gate({
            "status": "FAIL",
            "errors": ["canonical_market_writer_contract:unexpected_owner"],
        })
        self.assertTrue(gate["blocks_market_recovery"])
        self.assertEqual(gate["classification"], "GLOBAL_FAIL_BLOCKS_MARKET_RECOVERY_FAIL_SAFE")

    def _assess(self, consistency: dict) -> dict:
        now = datetime.fromisoformat("2026-09-10T10:24:00+08:00")
        current = {
            "market_date": "2026-09-10",
            "captured_at": "2026-09-10T10:15:00+08:00",
            "latest_valid_node": "1015",
            "node_status": "READY",
            "rules_version": "V2.2.31",
        }

        def fake_load(path: Path, default=None):
            text = str(path)
            if text.endswith("runtime_policy.json"):
                return {"self_healing": {"enabled": True, "watchdog_trigger_age_seconds": 780, "minimum_retrigger_seconds": 480, "max_snapshot_repair_attempts_per_hour": 2}}
            if text.endswith("a_share_trading_calendar_2026.json"):
                return {"closed_dates": []}
            if text.endswith("CURRENT.json"):
                return current
            if text.endswith("runtime_health.json"):
                return {"status": "PASS"}
            if text.endswith("system_consistency.json"):
                return consistency
            if text.endswith("self_healing_status.json"):
                return {}
            if text.endswith("query_context.json") or text.endswith("decision_context.json"):
                return {}
            return default

        with (
            mock.patch.object(runtime_self_heal, "load_json", side_effect=fake_load),
            mock.patch.object(runtime_self_heal, "master_version", return_value="V2.2.31"),
        ):
            return runtime_self_heal.assess(now)

    def test_nonblocking_global_fail_does_not_preempt_expected_pulse_recovery(self):
        status = self._assess({
            "status": "FAIL",
            "errors": ["historical_trade_case_mapping:2026-09-09 13:30:21:515220:case_count=0"],
        })
        self.assertEqual(status["classification"], "EXPECTED_PULSE_MISSING")
        self.assertEqual(status["recommended_action"], "REFRESH_SNAPSHOT")
        self.assertEqual(status["system_consistency_status"], "FAIL")
        self.assertFalse(status["market_recovery_consistency_gate"]["blocks_market_recovery"])

    def test_unknown_global_fail_preempts_market_recovery(self):
        status = self._assess({
            "status": "FAIL",
            "errors": ["canonical_market_writer_contract:unexpected_owner"],
        })
        self.assertEqual(status["classification"], "CONSISTENCY_REGRESSION")
        self.assertEqual(status["recommended_action"], "ESCALATE")
        self.assertTrue(status["market_recovery_consistency_gate"]["blocks_market_recovery"])

    def test_health_guard_only_allows_fresh_already_admitted_safe_recovery(self):
        source = (ROOT / "scripts" / "production_health_guard.py").read_text(encoding="utf-8")
        self.assertIn('self_healing_age <= 120', source)
        self.assertIn('"REFRESH_SNAPSHOT", "REBUILD_DERIVED_CONTEXTS", "SYNC_RULES_VERSION_METADATA"', source)
        self.assertIn('"status": overall', source)
        self.assertIn('if overall == "BLOCKED" and safe_recovery_admitted:', source)


if __name__ == "__main__":
    unittest.main()
