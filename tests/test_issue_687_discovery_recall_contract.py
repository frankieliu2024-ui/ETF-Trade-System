from __future__ import annotations

import unittest
from pathlib import Path

from scripts import formal_etf_opportunity_discovery as discovery


def row(code: str, change: float, *, r60: float | None = 0.0, name: str | None = None) -> dict:
    return {
        "code": code, "name": name or f"主题{code}ETF", "market_id": 1,
        "price": 1.0, "change_pct": change, "amount": 100_000_000.0,
        "volume_ratio": 1.2, "return_60d_pct": r60,
        "listing_date": "2026-08-01",
    }


def hist(n: int, slope: float = 0.002) -> list[dict]:
    out = []
    for i in range(n):
        month = 6 + i // 28
        day = 1 + i % 28
        out.append({"date": f"2026-{month:02d}-{day:02d}", "close": 1 + i * slope, "amount": 100_000_000.0})
    return out


class DiscoveryRecallContractTests(unittest.TestCase):
    def test_relative_strong_absolute_negative_can_receive_validation(self) -> None:
        rows = [
            row("510001", -3.0), row("510002", -2.5), row("510003", -2.0),
            row("513350", -0.2, name="标普油气ETF"),
        ]
        queue = discovery._bounded_prefilter(rows, set(), "2026-09-18")
        target = next(x for x in queue if x["code"] == "513350")
        self.assertIn("NEW_RELATIVE_DIVERGENCE", target["_discovery_information_classes"])
        result = discovery.discover_formal_candidates(
            Path("."), market_date="2026-09-18", managed_codes=set(),
            spot_rows=rows, history_by_code={"513350": hist(70), "510001": hist(70), "510002": hist(70), "510003": hist(70)},
        )
        self.assertIn("513350", {x["code"] for x in result["candidates"]})

    def test_ordinary_negative_noise_is_not_promoted_by_relative_path(self) -> None:
        rows = [row("510001", -1.0), row("510002", -0.9), row("510003", -1.1), row("510004", -1.0)]
        queue = discovery._bounded_prefilter(rows, set(), "2026-09-18")
        self.assertNotIn("510004", {x["code"] for x in queue})

    def test_short_history_is_limited_not_synthesized(self) -> None:
        rows = [row("510001", -3.0), row("510002", -2.5), row("588999", 0.2, r60=None, name="新主题ETF")]
        result = discovery.discover_formal_candidates(
            Path("."), market_date="2026-09-18", managed_codes=set(),
            spot_rows=rows, history_by_code={"588999": hist(30), "510001": hist(70), "510002": hist(70)},
        )
        item = next(x for x in result["candidates"] if x["code"] == "588999")
        self.assertEqual(item["historical_context"]["status"], "SHORT_HISTORY_LIMITED")
        self.assertEqual(item["surfaced_states"], [])
        self.assertFalse(item["trial_confirm_permission"])

    def test_too_short_history_still_fails_closed(self) -> None:
        rows = [row("510001", -3.0), row("510002", -2.5), row("588998", 0.2, r60=None)]
        result = discovery.discover_formal_candidates(
            Path("."), market_date="2026-09-18", managed_codes=set(),
            spot_rows=rows, history_by_code={"588998": hist(10), "510001": hist(70), "510002": hist(70)},
        )
        self.assertNotIn("588998", {x["code"] for x in result["candidates"]})

    def test_new_information_classes_precede_persistent_structure(self) -> None:
        rows = [
            row("510001", -2.0, r60=20.0),
            row("510002", -1.8, r60=18.0),
            row("510003", 0.2, r60=20.0),
            row("510004", -0.1, r60=0.0),
        ]
        queue = discovery._bounded_prefilter(rows, set(), "2026-09-18")
        first = queue[0]["_discovery_information_classes"]
        self.assertTrue("NEW_RELATIVE_DIVERGENCE" in first or "STRUCTURAL_CHANGE" in first)

    def test_large_early_class_cannot_starve_later_information_classes(self) -> None:
        rows = [
            row(f"51{i:04d}", -0.1, r60=0.0, name=f"相对强势{i}ETF")
            for i in range(20)
        ]
        # Make the broad median sufficiently weak so the first group is all
        # NEW_RELATIVE_DIVERGENCE, then add distinct later-class opportunities.
        rows += [row(f"52{i:04d}", -3.0, r60=0.0, name=f"基准{i}ETF") for i in range(20)]
        structural = row("530001", 1.0, r60=0.0, name="结构变化ETF")
        short = row("530002", 0.2, r60=None, name="短历史ETF")
        persistent = row("530003", 0.2, r60=12.0, name="持续结构ETF")
        rows += [structural, short, persistent]

        queue = discovery._bounded_prefilter(rows, set(), "2026-09-18")
        first_twelve = queue[:12]
        classes = {
            info_class
            for item in first_twelve
            for info_class in item["_discovery_information_classes"]
        }
        self.assertIn("NEW_RELATIVE_DIVERGENCE", classes)
        self.assertIn("STRUCTURAL_CHANGE", classes)
        self.assertIn("SHORT_HISTORY_CURRENT_CHANGE", classes)
        self.assertIn("PERSISTENT_STRUCTURE", classes)

    def test_bounded_diversity_does_not_strand_remaining_work(self) -> None:
        rows = [
            row(f"51{i:04d}", -0.1, r60=0.0, name=f"相对强势{i}ETF")
            for i in range(8)
        ]
        rows += [row(f"52{i:04d}", -3.0, r60=0.0, name=f"基准{i}ETF") for i in range(8)]
        queue = discovery._bounded_prefilter(rows, set(), "2026-09-18")
        self.assertGreaterEqual(len(queue), 8)
        self.assertEqual(len({item["code"] for item in queue}), len(queue))

    def test_discovery_classes_do_not_grant_trade_authority(self) -> None:
        rows = [row("510001", -3.0), row("510002", -2.5), row("513350", -0.2)]
        result = discovery.discover_formal_candidates(
            Path("."), market_date="2026-09-18", managed_codes=set(),
            spot_rows=rows, history_by_code={"513350": hist(70), "510001": hist(70), "510002": hist(70)},
        )
        item = next(x for x in result["candidates"] if x["code"] == "513350")
        self.assertEqual(item["category"], "OBSERVATION_EVALUATION_INPUT")
        self.assertFalse(item["auto_promote_to_observation"])
        self.assertFalse(item["trial_confirm_permission"])
        self.assertIsNone(item["trade_signal"])


if __name__ == "__main__":
    unittest.main()
