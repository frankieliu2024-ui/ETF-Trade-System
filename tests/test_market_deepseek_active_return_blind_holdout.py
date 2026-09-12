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


def _compact_blind_holdout_result(result: dict) -> dict:
    """Project the immutable blind result into a CI-log-safe audit summary.

    This is display-only: it does not recompute, filter, round, rank, or reinterpret
    any validation result.
    """
    hypotheses = []
    for hypothesis in result.get("hypotheses") or []:
        hypotheses.append(
            {
                "hypothesis_id": hypothesis.get("hypothesis_id"),
                "base_evidence": hypothesis.get("base_evidence"),
                "condition": hypothesis.get("condition"),
                "selected_signal_dates": hypothesis.get("selected_signal_dates"),
                "condition_coverage": hypothesis.get("condition_coverage"),
                "horizons_with_n15": hypothesis.get("horizons_with_n15"),
                "passed_horizons": hypothesis.get("passed_horizons"),
                "classification": hypothesis.get("classification"),
                "horizons": hypothesis.get("horizons"),
            }
        )
    return {
        "schema_version": result.get("schema_version"),
        "status": result.get("status"),
        "research_only": result.get("research_only"),
        "holdout": result.get("holdout"),
        "holdout_signal_date_count": result.get("holdout_signal_date_count"),
        "feature_semantics": result.get("feature_semantics"),
        "baseline_parity_status": (result.get("baseline_parity") or {}).get("status"),
        "hypotheses": hypotheses,
        "any_blind_supported": result.get("any_blind_supported"),
        "production_integration": result.get("production_integration"),
        "trade_signal": result.get("trade_signal"),
        "master_override": result.get("master_override"),
        "second_deepseek_call": result.get("second_deepseek_call"),
    }


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
        compact = _compact_blind_holdout_result(result)
        print("DEEPSEEK_ACTIVE_RETURN_BLIND_HOLDOUT_COMPACT=" + json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
        self.assertEqual(result["status"], "PASS", result.get("baseline_parity"))
        self.assertEqual(result["baseline_parity"]["status"], "PASS")
        self.assertEqual(len(result["hypotheses"]), 3)
        self.assertEqual([h["hypothesis_id"] for h in compact["hypotheses"]], [h["hypothesis_id"] for h in FROZEN_HYPOTHESES])
        self.assertFalse(result["production_integration"])
        self.assertIsNone(result["trade_signal"])
        self.assertFalse(result["master_override"])
        self.assertFalse(result["second_deepseek_call"])


if __name__ == "__main__":
    unittest.main()
