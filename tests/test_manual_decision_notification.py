from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import notification_center as center
import notification_materiality_guard as guard


class ManualDecisionNotificationTests(unittest.TestCase):
    def _formal_event(self, decision_id: str, status: str, risk: str, *, delivery: str = "UNSEEN") -> dict:
        return {
            "event_type": "FORMAL_DECISION",
            "decision_id": decision_id,
            "decision_time_beijing": "2026-09-15T11:00:00+08:00",
            "candidate_code": "515880",
            "candidate_name": "通信ETF",
            "interactive_delivery": {
                "mode": "LIVE_CHAT" if delivery == "CONSUMED" else "UNSPECIFIED",
                "status": delivery,
                "request_id": f"req-{decision_id}",
                "consumed_at_beijing": "2026-09-15T11:00:05+08:00" if delivery == "CONSUMED" else "",
            },
            "formal_decision": {
                "opportunity_status": status,
                "risk_permission": risk,
                "main_candidate": f"通信ETF（515880）｜{status}",
                "amount_action": "新增0元；持有管理。",
                "decisive_reason": "test",
            },
        }

    def test_same_interactively_consumed_result_is_suppressed(self):
        previous = self._formal_event("d1", "观察机会", "允许Trial")
        latest = self._formal_event("d2", "Trial机会", "允许Trial", delivery="CONSUMED")
        with patch.object(center, "_formal_decision_events", return_value=[previous, latest]), \
             patch.object(center, "now", return_value=center.parse_notification_time("2026-09-15T11:00:10+08:00")):
            self.assertIsNone(center.formal_decision_change_event())

    def test_unseen_material_result_still_notifies(self):
        previous = self._formal_event("d1", "观察机会", "允许Trial")
        latest = self._formal_event("d2", "Trial机会", "允许Trial")
        with patch.object(center, "_formal_decision_events", return_value=[previous, latest]), \
             patch.object(center, "now", return_value=center.parse_notification_time("2026-09-15T11:00:10+08:00")):
            event = center.formal_decision_change_event()
        self.assertIsNotNone(event)
        self.assertEqual(event["related_decision_id"], "d2")

    def test_system_blocker_requires_decision_critical_blockage(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "self_healing_status.json").write_text(json.dumps({
                "classification": "CONSISTENCY_REGRESSION",
                "recommended_action": "ESCALATE",
                "checked_at": "2026-09-15T11:00:00+08:00",
            }), encoding="utf-8")
            (state / "e2e_status.json").write_text(json.dumps({
                "status": "DEGRADED",
                "blockers": [],
                "components": {
                    "market": {"status": "READY"},
                    "account": {"status": "READY"},
                    "risk": {"status": "READY"},
                    "decision_context": {"status": "READY"},
                    "maintenance": {"status": "DEGRADED"},
                },
            }), encoding="utf-8")
            with patch.object(center, "STATE", state), patch.object(guard, "STATE", state):
                self.assertIsNone(center.system_event())

    def test_persistent_decision_critical_blockage_remains_eligible(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "self_healing_status.json").write_text(json.dumps({
                "classification": "PERSISTENT_RUNTIME_FAILURE",
                "recommended_action": "ESCALATE",
                "checked_at": "2026-09-15T11:00:00+08:00",
            }), encoding="utf-8")
            (state / "e2e_status.json").write_text(json.dumps({
                "status": "BLOCKED",
                "blockers": ["market"],
                "components": {
                    "market": {"status": "BLOCKED"},
                    "account": {"status": "READY"},
                    "risk": {"status": "READY"},
                    "decision_context": {"status": "BLOCKED"},
                },
            }), encoding="utf-8")
            with patch.object(center, "STATE", state), patch.object(guard, "STATE", state):
                event = center.system_event()
                self.assertIsNotNone(event)
                self.assertEqual(guard.notification_evidence_error(event), "")


if __name__ == "__main__":
    unittest.main()
