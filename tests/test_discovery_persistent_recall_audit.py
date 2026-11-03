"""PIT-safe, read-only regressions for persistent-trend Discovery recall.

These tests intentionally do not grant observation, Trial, Confirm or trade authority.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from scripts import formal_etf_opportunity_discovery as discovery


def history(closes: list[float]) -> list[dict]:
    start = date(2026, 6, 1)
    return [
        {"date": (start + timedelta(days=i)).isoformat(),
         "close": close, "amount": 150_000_000.0}
        for i, close in enumerate(closes)
    ]


def spot(code: str, *, change: float, r60: float, name: str) -> dict:
    return {
        "code": code, "name": name, "market_id": 0,
        "price": 1.0, "change_pct": change, "amount": 150_000_000.0,
        "volume_ratio": 1.1, "return_60d_pct": r60,
    }


class PersistentRecallAuditTests(unittest.TestCase):
    def test_existing_persistent_state_is_not_a_new_state(self) -> None:
        rows = history([1.0 + 0.002 * i for i in range(90)])
        now = discovery.classify_states(rows)
        prior = discovery.classify_states(rows[:-1])
        self.assertIn("PERSISTENT_TREND", {x["state"] for x in now})
        self.assertIn("PERSISTENT_TREND", {x["state"] for x in prior})
        self.assertEqual(
            {x["state"] for x in now} - {x["state"] for x in prior},
            set(),
        )

    def test_persistent_trend_can_pass_without_new_state_when_aligned(self) -> None:
        rows = history([1.0 + 0.002 * i for i in range(90)])
        row = spot("159928", change=0.1, r60=8.0, name="消费ETF测试")
        row["_discovery_information_classes"] = ["PERSISTENT_STRUCTURE"]
        item = discovery._candidate(row, rows, rows[-1]["date"])
        self.assertIsNotNone(item)
        self.assertEqual(item["entered_states"], [])
        self.assertIn("PERSISTENT_TREND", {x["state"] for x in item["surfaced_states"]})
        self.assertFalse(item["trial_confirm_permission"])
        self.assertIsNone(item["trade_signal"])

    def test_positive_twenty_day_return_alone_is_not_a_discovery_class(self) -> None:
        # Current implementation has no 5d/20d return field in cheap prefilter.
        # A flat day and negative 60d return do not qualify merely because
        # historical 20d performance is strong; this is an audit characterization,
        # not an assertion that the ETF is a qualified trade.
        row = spot("159529", change=0.0, r60=-2.0, name="标普消费ETF测试")
        self.assertEqual(discovery._information_classes(row, 0.0), [])


if __name__ == "__main__":
    unittest.main()
