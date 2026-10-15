from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_self_heal  # noqa: E402


class FormalHandoffSelfHealGuardTests(unittest.TestCase):
    def _fixture(self, complete: bool):
        temp = Path(tempfile.mkdtemp())
        (temp / "requests" / "live_snapshot").mkdir(parents=True)
        (temp / "events" / "reviews").mkdir(parents=True)
        parent = {
            "request_type": "MARKET_QUOTE_REFRESH",
            "source": "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
            "request_id": "formal-current",
            "requested_at_beijing": "2026-10-15T19:53:18+08:00",
        }
        (temp / "requests" / "live_snapshot" / "formal-current.json").write_text(
            json.dumps(parent), encoding="utf-8"
        )
        e2e = {
            "components": {
                "decision_context": {
                    "formal_reply_gate": {
                        "request_id": "formal-current",
                        "reply_freezable": True,
                    }
                }
            }
        }
        (temp / "e2e_status.json").write_text(json.dumps(e2e), encoding="utf-8")
        if complete:
            for name, payload in {
                "formal-current__business.json": {
                    "request_type": "BUSINESS_DECISION_SOURCE",
                    "parent_request_id": "formal-current",
                },
                "formal-current__formal_decision_completion.json": {
                    "request_type": "STATE_SYNC_ONLY",
                    "source": "CHATGPT_FORMAL_DECISION_COMPLETION",
                    "formal_fact_type": "FORMAL_DECISION_COMPLETION",
                    "parent_request_id": "formal-current",
                },
            }.items():
                (temp / "requests" / "live_snapshot" / name).write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            (temp / "events" / "reviews" / "formal-occurrence.json").write_text(
                json.dumps({"parent_request_id": "formal-current"}), encoding="utf-8"
            )
        return temp

    def test_ready_parent_without_projection_is_escalated_without_replay(self):
        temp = self._fixture(complete=False)
        with (
            mock.patch.object(runtime_self_heal, "ROOT", temp),
            mock.patch.object(runtime_self_heal, "E2E_STATUS_PATH", temp / "e2e_status.json"),
        ):
            path, parent_id, missing = runtime_self_heal.formal_parent_handoff_gap()
        self.assertEqual(parent_id, "formal-current")
        self.assertEqual(path, "requests/live_snapshot/formal-current.json")
        self.assertEqual(missing, "BDS,COMPLETION,FORMAL_EVENT")

    def test_complete_parent_has_no_handoff_gap(self):
        temp = self._fixture(complete=True)
        with (
            mock.patch.object(runtime_self_heal, "ROOT", temp),
            mock.patch.object(runtime_self_heal, "E2E_STATUS_PATH", temp / "e2e_status.json"),
        ):
            self.assertEqual(runtime_self_heal.formal_parent_handoff_gap(), (None, None, None))


if __name__ == "__main__":
    unittest.main()
