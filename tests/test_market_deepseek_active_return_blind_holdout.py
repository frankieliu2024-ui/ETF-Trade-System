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
        self.assertTrue(condition_pass(0.50, "gte", 0.50))
        self.assertTrue(condition_pass(0.02, "gte", 0.02))
        self.assertTrue(condition_pass(0.02, "lte", 0.02))

    @unittest.skipUnless(os.environ.get("GITHUB_EVENT_NAME") == "pull_request", "one-time real-data blind evidence runs only on PR")
    def test_current_repository_2026_blind_holdout(self) -> None:
        result = run_validation()
        print("DEEPSEEK_ACTIVE_RETURN_BLIND_HOLDOUT=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        self.assertEqual(result["status"], "PASS", result.get("baseline_parity"))
        self.assertEqual(result["baseline_parity"]["status"], "PASS")
        self.assertEqual(len(result["hypotheses"]), 3)
        self.assertFalse(result["production_integration"])
        self.assertIsNone(result["trade_signal"])
        self.assertFalse(result["master_override"])
        self.assertFalse(result["second_deepseek_call"])


if __name__ == "__main__":
    unittest.main()
