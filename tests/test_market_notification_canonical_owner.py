from __future__ import annotations

import unittest
from pathlib import Path

from scripts import notification_center as center
from tests.test_notification_ssot_closeout import NotificationSsotCloseoutTests


ROOT = Path(__file__).resolve().parents[1]


class CanonicalNotificationOwnerIntegrationTests(unittest.TestCase):
    """PR-gated integration checks for the single canonical notification owner."""

    def test_legacy_decision_sender_is_absent(self):
        self.assertFalse((ROOT / "scripts" / "send_decision_notification.py").exists())

    def test_unknown_interrupt_event_fails_closed(self):
        event = {
            "event_type": "UNKNOWN_FREE_FORM",
            "security_code": "518880",
            "security_name": "黄金ETF",
            "content": "自由文案不应进入发送链",
            "confirmation_context": {},
        }
        self.assertIsNone(center.render_canonical_notification(event))

    def test_report_delivery_bypasses_interrupt_renderer(self):
        event = {
            "event_type": "REPORT_DELIVERY_REQUEST",
            "title": "【交易复盘】完整原文",
            "content": "# 正式报告\n\n保持原文",
            "full_content": "# 正式报告\n\n保持原文",
            "notification_channel": "REPORT",
            "delivery_mode": "FULL_REPORT",
        }
        self.assertIs(center.render_canonical_notification(event), event)


if __name__ == "__main__":
    unittest.main()
