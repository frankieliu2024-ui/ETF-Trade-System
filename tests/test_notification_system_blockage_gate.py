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

import notification_materiality_guard as guard


class NotificationSystemBlockageGateTests(unittest.TestCase):
    def _write(self, state: Path, name: str, payload: dict) -> None:
        (state / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_maintenance_only_degraded_does_not_qualify_system_notification(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._write(state, "workflow_failure_diagnostic.json", {
                "recommended_action": "ESCALATE_WITH_DIAGNOSTIC",
                "run_id": "123",
                "safety": {"head_is_current_main": True},
            })
            self._write(state, "e2e_status.json", {
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
            })
            with patch.object(guard, "STATE", state):
                event = {"type": "系统异常", "source": "workflow_failure_diagnostic"}
                self.assertIn("no current decision-critical blockage", guard.notification_evidence_error(event))

    def test_current_decision_critical_blockage_allows_escalated_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._write(state, "workflow_failure_diagnostic.json", {
                "recommended_action": "ESCALATE_WITH_DIAGNOSTIC",
                "run_id": "123",
                "safety": {"head_is_current_main": True},
            })
            self._write(state, "e2e_status.json", {
                "status": "BLOCKED",
                "blockers": ["market"],
                "components": {"market": {"status": "BLOCKED"}},
            })
            with patch.object(guard, "STATE", state):
                event = {"type": "系统异常", "source": "workflow_failure_diagnostic"}
                self.assertEqual(guard.notification_evidence_error(event), "")


if __name__ == "__main__":
    unittest.main()
