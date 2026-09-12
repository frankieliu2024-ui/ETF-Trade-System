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


def _audit_horizon(horizon: dict) -> list:
    """Encode every field needed to audit the frozen blind gate compactly.

    Array schema:
    [horizon, filtered_n, filtered_effect, filtered_positive_rate,
     baseline_effect, baseline_positive_rate, concentration, gate_pass]

    For active_migration_spread ``effect`` is the mean after the frozen 20bps
    round-trip cost. For right_tail_holding it is the mean relative return.
    The hypothesis-level base_evidence identifies which interpretation applies.
    """
    baseline = horizon.get("baseline") or {}
    filtered = horizon.get("filtered") or {}
    filtered_effect = filtered.get("mean_net_after_20bps", filtered.get("mean"))
    baseline_effect = baseline.get("mean_net_after_20bps", baseline.get("mean"))
    concentration = horizon.get("max_single_asset_participation_share")
    if concentration is None:
        concentration = horizon.get("max_single_asset_event_share")
    return [
        horizon.get("horizon"),
        filtered.get("n"),
        filtered_effect,
        filtered.get("positive_rate"),
        baseline_effect,
        baseline.get("positive_rate"),
        concentration,
        horizon.get("blind_gate_pass"),
    ]


def _compact_blind_holdout_result(result: dict) -> dict:
    """Project the immutable blind result into the consistency detail window.

    ``check_system_consistency_core`` intentionally retains only the final 1000
    characters of unittest output.  Keep this marker below 800 UTF-8 bytes so
    the complete evidence plus unittest trailer survives that canonical owner.
    This is display-only: it never recomputes, filters, rounds, ranks, or
    reinterprets the validator result.
    """
    hypotheses = []
    for hypothesis in result.get("hypotheses") or []:
        hypotheses.append(
            [
                hypothesis.get("hypothesis_id"),
                hypothesis.get("base_evidence"),
                hypothesis.get("selected_signal_dates"),
                hypothesis.get("condition_coverage"),
                hypothesis.get("horizons_with_n15"),
                hypothesis.get("passed_horizons"),
                hypothesis.get("classification"),
                [_audit_horizon(h) for h in hypothesis.get("horizons") or []],
            ]
        )
    return {
        "v": result.get("schema_version"),
        "s": result.get("status"),
        "d": result.get("holdout"),
        "n": result.get("holdout_signal_date_count"),
        "p": (result.get("baseline_parity") or {}).get("status"),
        "h": hypotheses,
        "a": result.get("any_blind_supported"),
        "pi": result.get("production_integration"),
        "ts": result.get("trade_signal"),
        "mo": result.get("master_override"),
        "ds2": result.get("second_deepseek_call"),
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
        marker = "DEEPSEEK_ACTIVE_RETURN_BLIND_HOLDOUT_COMPACT=" + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(marker.encode("utf-8")), 800, "compact blind evidence must survive the canonical 1000-char consistency detail window")
        print(marker)
        self.assertEqual(result["status"], "PASS", result.get("baseline_parity"))
        self.assertEqual(result["baseline_parity"]["status"], "PASS")
        self.assertEqual(len(result["hypotheses"]), 3)
        self.assertEqual([h[0] for h in compact["h"]], [h["hypothesis_id"] for h in FROZEN_HYPOTHESES])
        self.assertFalse(result["production_integration"])
        self.assertIsNone(result["trade_signal"])
        self.assertFalse(result["master_override"])
        self.assertFalse(result["second_deepseek_call"])


if __name__ == "__main__":
    unittest.main()
