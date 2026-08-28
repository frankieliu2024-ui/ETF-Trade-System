from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import market_notification_common as common
import send_regional_session_summary as regional


class ApacLateUpdateGuardTests(unittest.TestCase):
    def _event(self, delta, title="【收盘总结】亚太香港收盘后区域判断发生实质变化"):
        return {
            "event_type": "APAC_SESSION_SUMMARY",
            "title": title,
            "confirmation_context": {
                "session_node": "HK_LATE_UPDATE",
                "hstech_change_since_primary_pct": delta,
            },
        }

    def test_sender_threshold_matches_regional_producer(self):
        self.assertEqual(
            common.APAC_LATE_HK_MIN_DELTA_PCT,
            regional.APAC_LATE_HK_CHANGE_PCT,
        )

    def test_missing_comparable_hstech_delta_is_rejected(self):
        error = common._late_apac_update_error(self._event(None))
        self.assertIn("no comparable HSTECH delta", error)

    def test_subthreshold_hstech_move_is_rejected(self):
        error = common._late_apac_update_error(self._event(0.79))
        self.assertIn("below", error)

    def test_material_hstech_move_is_allowed(self):
        self.assertEqual(common._late_apac_update_error(self._event(-0.80)), "")

    def test_missing_primary_summary_recovery_remains_allowed(self):
        event = self._event(
            None,
            title="【收盘总结】亚太香港收盘后主总结兜底",
        )
        self.assertEqual(common._late_apac_update_error(event), "")

    def test_unrelated_notifications_are_untouched(self):
        event = {
            "event_type": "MARKET_VALUE_ALERT",
            "title": "【市场异动】测试",
            "confirmation_context": {"session_node": "HK_LATE_UPDATE"},
        }
        self.assertEqual(common._late_apac_update_error(event), "")


if __name__ == "__main__":
    unittest.main()
