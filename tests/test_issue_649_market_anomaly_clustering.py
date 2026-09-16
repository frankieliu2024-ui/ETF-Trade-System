from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts import market_notification_common as common
from scripts import send_market_shock_notification as producer


class Issue649MarketAnomalyClusteringTests(unittest.TestCase):
    def event(self, code="000688", category="EXTREME", magnitude=3.0, direction="UP",
              event_type="MARKET_VALUE_ALERT", market="A_SHARE"):
        return {
            "event_type": event_type,
            "security_code": code,
            "security_name": code,
            "confirmation_context": {
                "market": market,
                "market_date": "2026-09-16",
                "direction": direction,
                "event_category": category,
                "event_magnitude_pct": magnitude,
                "market_as_of_beijing": "2026-09-16T10:30:00+08:00",
            },
        }

    def state_for(self, items):
        td = tempfile.TemporaryDirectory()
        state = Path(td.name)
        (state / "notification_center.json").write_text(
            json.dumps({"notifications": items}), encoding="utf-8"
        )
        return td, state

    def duplicate(self, current, prior):
        td, state = self.state_for([prior])
        with patch.object(producer._legacy, "STATE", state):
            return producer._recent_duplicate(current)

    def test_same_object_extreme_first_is_not_duplicate(self):
        self.assertFalse(self.duplicate(self.event(magnitude=3.0), self.event(magnitude=2.0, event_type="OTHER")))

    def test_same_object_extreme_small_progression_is_suppressed(self):
        self.assertTrue(self.duplicate(self.event(magnitude=3.4), self.event(magnitude=3.0)))

    def test_same_object_extreme_first_material_upgrade_is_allowed(self):
        self.assertFalse(self.duplicate(self.event(magnitude=4.2), self.event(magnitude=3.0)))

    def test_same_object_extreme_second_material_upgrade_is_suppressed(self):
        prior = [self.event(magnitude=3.0), self.event(magnitude=4.2)]
        td, state = self.state_for(prior)
        with patch.object(producer._legacy, "STATE", state):
            self.assertTrue(producer._recent_duplicate(self.event(magnitude=6.0)))

    def test_sudden_to_extreme_is_allowed(self):
        self.assertFalse(self.duplicate(self.event(category="EXTREME", magnitude=3.0), self.event(category="SUDDEN", magnitude=1.0)))

    def test_extreme_to_reversal_is_allowed(self):
        self.assertFalse(self.duplicate(self.event(category="REVERSAL", magnitude=2.0), self.event(category="EXTREME", magnitude=3.0)))

    def test_tech_index_and_etf_share_episode(self):
        prior = self.event(code="000688", magnitude=3.0)
        current = self.event(code="588000", magnitude=3.1)
        self.assertTrue(self.duplicate(current, prior))

    def test_tech_growth_etf_and_chinext_share_episode(self):
        prior = self.event(code="159781", magnitude=3.0)
        current = self.event(code="399006", magnitude=3.1)
        self.assertTrue(self.duplicate(current, prior))

    def test_513350_is_not_in_tech_episode(self):
        prior = self.event(code="000688", magnitude=3.0)
        current = self.event(code="513350", magnitude=3.1)
        self.assertFalse(self.duplicate(current, prior))

    def test_catl_holding_risk_is_not_clustered(self):
        prior = self.event(code="000688", magnitude=3.0)
        current = self.event(code="300750", magnitude=3.1)
        self.assertFalse(self.duplicate(current, prior))

    def test_cross_sectional_divergence_remains_separate_category(self):
        prior = self.event(code="000688", category="DIVERGENCE", magnitude=3.0)
        current = self.event(code="588000", category="EXTREME", magnitude=3.1)
        self.assertFalse(self.duplicate(current, prior))

    def test_fixed_summary_is_not_market_anomaly_cluster(self):
        event = self.event(event_type="A_SHARE_SESSION_SUMMARY")
        self.assertEqual(producer._structure_cluster_id(event), "")

    def test_us_open_and_close_are_not_clustered(self):
        prior = self.event(code="US_TECH_DIVERGENCE", market="US", category="DIVERGENCE")
        prior["confirmation_context"]["session"] = "REGULAR"
        current = dict(prior)
        current["confirmation_context"] = dict(prior["confirmation_context"], session="POST_MARKET")
        self.assertFalse(common._same_user_level_fact(prior, current))

    def test_provider_only_change_has_no_cluster_identity(self):
        event = self.event()
        event["confirmation_context"]["provider"] = "other"
        self.assertEqual(producer._structure_cluster_id(event), "A_SHARE_TECH_GROWTH_EPISODE")

    def test_observe_trial_confirm_are_protected(self):
        for kind in ("观察机会", "Trial机会", "Confirm机会"):
            event = self.event(event_type=kind)
            self.assertEqual(common._event_category(event), "EXTREME")

    def test_opportunity_invalidation_and_holding_action_are_not_clustered(self):
        for kind in ("机会失效", "持仓动作", "风险许可"):
            event = self.event(event_type=kind)
            self.assertEqual(producer._structure_cluster_id(event), "A_SHARE_TECH_GROWTH_EPISODE")

    def test_trade_account_system_recovery_paths_are_not_rewritten(self):
        for kind in ("PENDING_EXECUTION_CONFIRMATION", "ACCOUNT_FACT_CONFIRMATION", "SYSTEM_RUNTIME_BLOCKER"):
            event = self.event(event_type=kind)
            self.assertEqual(common._is_protected_event(event), kind in common.PROTECTED_EVENT_TYPES)

    def test_no_global_daily_cap_is_introduced(self):
        self.assertFalse(any("daily" in name.lower() and "limit" in name.lower()
                              for name in dir(producer)))


if __name__ == "__main__":
    unittest.main()
