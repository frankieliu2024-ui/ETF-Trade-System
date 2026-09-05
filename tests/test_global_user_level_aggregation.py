from __future__ import annotations

import unittest

from scripts import market_notification_common as common


class GlobalUserLevelAggregationTests(unittest.TestCase):
    def event(self, code, category, *, market="US", date="2026-09-04", session="REGULAR",
              direction="UP", magnitude=3.05, objects=None, family="US_SESSION_STRUCTURE"):
        return {
            "event_type": "MARKET_VALUE_ALERT",
            "security_code": code,
            "security_name": code,
            "created_at": "2026-09-05T00:35:18+08:00",
            "confirmation_context": {
                "market": market, "market_date": date, "session": session,
                "direction": direction, "event_category": category,
                "fact_family": family, "object_codes": objects or [code],
                "event_magnitude_pct": magnitude,
                "market_as_of_beijing": "2026-09-05T00:35:00+08:00",
            },
        }

    def test_same_object_open_to_extreme_is_absorbed_without_upgrade(self):
        prior = self.event("SOX", "OPEN_SIGNAL", magnitude=3.05)
        current = self.event("SOX", "EXTREME", magnitude=3.17)
        self.assertIsNotNone(common._find_aggregate_target([prior], current))

    def test_cross_object_divergence_requires_explicit_relation(self):
        prior = self.event("US_OPEN", "OPEN_SIGNAL", objects=["NDX", "SOX"])
        current = self.event("US_TECH_DIVERGENCE", "DIVERGENCE", objects=["NDX", "SOX"], magnitude=2.80)
        self.assertIsNotNone(common._find_aggregate_target([prior], current))
        unrelated = self.event("QQQ", "EXTREME", objects=["QQQ"], magnitude=3.1)
        self.assertIsNone(common._find_aggregate_target([prior], unrelated))

    def test_material_upgrade_and_reversal_are_not_absorbed(self):
        prior = self.event("SOX", "OPEN_SIGNAL", magnitude=3.05)
        self.assertIsNone(common._find_aggregate_target([prior], self.event("SOX", "EXTREME", magnitude=4.20)))
        self.assertIsNone(common._find_aggregate_target([prior], self.event("SOX", "REVERSAL", direction="DOWN", magnitude=3.2)))

    def test_protected_formal_event_is_not_absorbed(self):
        event = self.event("SOX", "EXTREME")
        event["event_type"] = "FORMAL_DECISION_MATERIAL_CHANGE"
        event["confirmation_context"]["opportunity_status"] = "Confirm机会"
        self.assertIsNone(common._find_aggregate_target([self.event("SOX", "OPEN_SIGNAL")], event))


if __name__ == "__main__":
    unittest.main()
