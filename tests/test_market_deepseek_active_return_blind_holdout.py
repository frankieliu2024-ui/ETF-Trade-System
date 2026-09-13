from __future__ import annotations

import json
import os
import unittest

from scripts.validate_deepseek_active_return_blind_holdout import (
    CONCENTRATION_LIMIT,
    FROZEN_HYPOTHESES,
    HOLDOUT_END,
    HOLDOUT_START,
    MIN_HOLDOUT_N,
    ROUND_TRIP_COST,
    condition_pass,
    run_validation,
)


def _h2_tail_marker(hypothesis: dict) -> str:
    """Persist only H2's frozen incremental gate result inside the 1000-char tail.

    Schema: [classification, passed_horizons,
      [[filtered_net, baseline_net, filtered_positive_rate,
        baseline_positive_rate, gate_pass], ... 5/10/20d]]

    This is projection-only. It does not recalculate or reinterpret the validator.
    """
    rows = []
    for horizon in hypothesis.get("horizons") or []:
        baseline = horizon.get("baseline") or {}
        filtered = horizon.get("filtered") or {}
        rows.append([
            filtered.get("mean_net_after_20bps", filtered.get("mean")),
            baseline.get("mean_net_after_20bps", baseline.get("mean")),
            filtered.get("positive_rate"),
            baseline.get("positive_rate"),
            horizon.get("blind_gate_pass"),
        ])
    payload = [hypothesis.get("classification"), hypothesis.get("passed_horizons"), rows]
    return "H2_BLIND=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class DeepSeekActiveReturnBlindHoldoutContractTest(unittest.TestCase):
    def test_frozen_contract_is_exact(self) -> None:
        self.assertEqual((HOLDOUT_START, HOLDOUT_END), ("2026-01-01", "2026-08-21"))
        self.assertEqual(MIN_HOLDOUT_N, 15)
        self.assertAlmostEqual(ROUND_TRIP_COST, 0.002)
        self.assertAlmostEqual(CONCENTRATION_LIMIT, 0.50)
        self.assertEqual(
            [h["hypothesis_id"] for h in FROZEN_HYPOTHESES],
            [
                "H1_breadth_regime_gate",
                "H2_dispersion_regime_gate",
                "H3_volatility_regime_gate",
            ],
        )
        self.assertEqual(
            [h["base_evidence"] for h in FROZEN_HYPOTHESES],
            ["active_migration_spread", "active_migration_spread", "right_tail_holding"],
        )
        self.assertTrue(condition_pass(0.50, "gte", 0.50))
        self.assertTrue(condition_pass(0.02, "gte", 0.02))
        self.assertTrue(condition_pass(0.02, "lte", 0.02))

    @unittest.skipUnless(os.environ.get("GITHUB_EVENT_NAME") == "pull_request", "one-time real-data blind evidence runs only on PR")
    def test_current_repository_2026_blind_holdout(self) -> None:
        result = run_validation()
        self.assertEqual(result["status"], "PASS", result.get("baseline_parity"))
        self.assertEqual(result["baseline_parity"]["status"], "PASS")
        self.assertEqual(len(result["hypotheses"]), 3)
        self.assertFalse(result["production_integration"])
        self.assertIsNone(result["trade_signal"])
        self.assertFalse(result["master_override"])
        self.assertFalse(result["second_deepseek_call"])

        h2_marker = _h2_tail_marker(result["hypotheses"][1])
        self.assertLess(len(h2_marker.encode("utf-8")), 240, "H2 marker must survive the canonical consistency detail tail")
        print(h2_marker)


if __name__ == "__main__":
    unittest.main()
