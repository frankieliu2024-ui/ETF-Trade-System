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


class NotificationSystemBlockageGateTests(unittest.TestCase):
    def _write(self, state: Path, name: str, payload: dict) -> None:
        (state / name).write_text(json.dumps(payload), encoding="utf-8")

    def _maintenance_only_e2e(self) -> dict:
        return {
            "status": "DEGRADED",
            "blockers": [],
            "degradations": ["maintenance"],
            "components": {
                "market": {"status": "READY"},
                "account": {"status": "READY"},
                "risk": {"status": "READY"},
                "decision_context": {"status": "READY"},
                "lifecycle": {"status": "READY"},
                "maintenance": {"status": "DEGRADED"},
            },
        }

    def test_maintenance_only_degraded_does_not_qualify_system_notification(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._write(state, "workflow_failure_diagnostic.json", {
                "recommended_action": "ESCALATE_WITH_DIAGNOSTIC",
                "run_id": "123",
                "safety": {"head_is_current_main": True},
            })
            self._write(state, "e2e_status.json", self._maintenance_only_e2e())
            with patch.object(guard, "STATE", state):
                event = {"type": "系统异常", "source": "workflow_failure_diagnostic"}
                self.assertIn("no current decision-critical blockage", guard.notification_evidence_error(event))

    def test_generation_path_also_suppresses_maintenance_only_escalation(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._write(state, "self_healing_status.json", {
                "classification": "CONSISTENCY_REGRESSION",
                "recommended_action": "ESCALATE",
                "checked_at": "2026-09-15T12:00:00+08:00",
            })
            self._write(state, "workflow_failure_diagnostic.json", {
                "recommended_action": "ESCALATE_WITH_DIAGNOSTIC",
                "run_id": "123",
                "safety": {"head_is_current_main": True},
            })
            self._write(state, "e2e_status.json", self._maintenance_only_e2e())
            with patch.object(guard, "STATE", state), patch.object(center, "STATE", state):
                self.assertIsNone(center.system_event())

    def test_current_decision_critical_blockage_allows_escalated_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._write(state, "workflow_failure_diagnostic.json", {
                "recommended_action": "ESCALATE_WITH_DIAGNOSTIC",
                "run_id": "123",
                "head_sha": "same",
                "main_head_sha": "same",
                "safety": {"head_is_current_main": True},
            })
            self._write(state, "e2e_status.json", {
                "status": "BLOCKED",
                "blockers": ["market"],
                "components": {"market": {"status": "BLOCKED"}},
            })
            with patch.object(guard, "STATE", state), patch.object(center, "STATE", state):
                event = {"type": "系统异常", "source": "workflow_failure_diagnostic"}
                self.assertEqual(guard.notification_evidence_error(event), "")
                generated = center.system_event()
                self.assertIsNotNone(generated)
                self.assertEqual(generated["source"], "workflow_failure_diagnostic")

    def test_missing_e2e_fails_closed_for_system_interrupt(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._write(state, "self_healing_status.json", {
                "classification": "CONSISTENCY_REGRESSION",
                "recommended_action": "ESCALATE",
                "checked_at": "2026-09-15T12:00:00+08:00",
            })
            with patch.object(guard, "STATE", state), patch.object(center, "STATE", state):
                blocked, reason = guard.decision_critical_blockage()
                self.assertFalse(blocked)
                self.assertEqual(reason, "e2e_status_missing")
                self.assertIsNone(center.system_event())


if __name__ == "__main__":
    unittest.main()
