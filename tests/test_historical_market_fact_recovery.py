import unittest
import json
from pathlib import Path

from scripts.historical_market_fact_recovery import (
    RecoveryClass,
    assess_recovery,
    build_provenance,
    assess_snapshot_document,
    should_replace_current,
)


SYMBOLS = ("000001.SH", "399006.SZ")


def row(symbol, date="2026-08-31", **extra):
    return {"symbol": symbol, "date": date, "open": 1, "high": 2, "low": 1, "close": 2, **extra}


class HistoricalMarketFactRecoveryTests(unittest.TestCase):
    def test_real_canonical_close_snapshot_is_replayable(self):
        path = Path(__file__).parents[1] / "data/market/snapshots/2026-08-31_150110.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        symbols = [row["thscode"] for row in snapshot["rows"]]
        result = assess_snapshot_document(snapshot, required_symbols=symbols, target_node="close")
        self.assertEqual(result.classification, RecoveryClass.RECOVERABLE_EXACT)
        self.assertEqual(result.coverage, 14)

    def test_complete_daily_bar_is_eod_equivalent_not_exact(self):
        result = assess_recovery(
            target_market_date="2026-08-31", target_node="close", required_symbols=SYMBOLS,
            rows=[row(symbol) for symbol in SYMBOLS], evidence_kind="DAILY_BAR",
        )
        self.assertEqual(result.classification, RecoveryClass.RECOVERABLE_EOD_EQUIVALENT)
        self.assertFalse(result.can_promote_to_current)

    def test_missing_object_is_partial(self):
        result = assess_recovery(
            target_market_date="2026-08-31", target_node="close", required_symbols=SYMBOLS,
            rows=[row(SYMBOLS[0])], evidence_kind="DAILY_BAR",
        )
        self.assertEqual(result.classification, RecoveryClass.PARTIALLY_RECOVERABLE)
        self.assertEqual(result.missing_symbols, (SYMBOLS[1],))

    def test_wrong_date_cannot_recover_intraday_target(self):
        result = assess_recovery(
            target_market_date="2026-08-20", target_node="10:37", required_symbols=SYMBOLS,
            rows=[row(symbol, date="2026-08-20") for symbol in SYMBOLS], evidence_kind="DAILY_BAR",
        )
        self.assertEqual(result.classification, RecoveryClass.RECOVERABLE_EOD_EQUIVALENT)
        self.assertFalse(result.can_promote_to_current)

    def test_exact_requires_original_observation_time(self):
        result = assess_recovery(
            target_market_date="2026-08-31", target_node="close", required_symbols=SYMBOLS,
            rows=[row(symbol) for symbol in SYMBOLS], evidence_kind="EXACT_INTRADAY",
            original_provider_observation_available=True,
        )
        self.assertEqual(result.classification, RecoveryClass.RECOVERABLE_EXACT)
        self.assertTrue(result.can_promote_to_current)

    def test_current_never_moves_back_for_historical_evidence(self):
        existing = {"market_date": "2026-09-01", "latest_valid_node": "live"}
        candidate = {"market_date": "2026-08-31", "recovery_classification": "RECOVERABLE_EXACT"}
        self.assertFalse(should_replace_current(existing, candidate))

    def test_provenance_does_not_fabricate_observation_time(self):
        provenance = build_provenance(
            target_market_date="2026-09-01", target_node="close",
            effective_market_time_beijing="2026-09-01T15:00:00+08:00",
            historical_retrieved_at_beijing="2026-09-02T10:00:00+08:00",
            provider="hithink-finance", source_reference="request-1",
            recovery_reason="missing close", evidence_grade="EOD_EQUIVALENT",
            coverage={"count": 14},
        )
        self.assertIsNone(provenance["original_provider_observation_time_beijing"])


if __name__ == "__main__":
    unittest.main()

