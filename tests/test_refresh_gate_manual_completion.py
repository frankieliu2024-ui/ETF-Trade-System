import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import refresh_gate


class ManualCompletionRequestBoundPitGuardTest(unittest.TestCase):
    def _write(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_accepts_same_parent_action_ready_query_time_pit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_dir = root / "requests" / "live_snapshot"
            query_context = root / "data" / "state" / "query_context.json"
            parent_id = "20260922_215438_chatgpt_decision_analysis"
            self._write(request_dir / f"{parent_id}.json", {
                "request_id": parent_id,
                "requested_at_beijing": "2026-09-22T21:54:38+08:00",
            })
            self._write(query_context, {
                "decision_fact_pack": {
                    "trigger": {"request_id": parent_id},
                    "formal_action_readiness": {
                        "status": "READY",
                        "ready": True,
                        "request_scoped_pit_resolved": True,
                    },
                    "market_quote": {
                        "mode": "QUERY_TIME_IMMEDIATE_REFRESH",
                        "decision_freshness": {
                            "request_time": "2026-09-22T13:54:38+00:00",
                            "resolved_post_request": True,
                        },
                    },
                }
            })
            req = {
                "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
                "parent_request_id": parent_id,
                "formal_decision": {"data_as_of_beijing": "2026-09-22T21:55:00+08:00"},
            }
            with patch.object(refresh_gate, "REQUEST_DIR", request_dir), patch.object(refresh_gate, "QUERY_CONTEXT", query_context):
                self.assertTrue(refresh_gate._manual_completion_request_bound_pit_ready(req))

    def test_rejects_mismatched_parent_or_unresolved_pit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_dir = root / "requests" / "live_snapshot"
            query_context = root / "data" / "state" / "query_context.json"
            parent_id = "parent"
            self._write(request_dir / f"{parent_id}.json", {"request_id": parent_id})
            self._write(query_context, {
                "decision_fact_pack": {
                    "trigger": {"request_id": "other"},
                    "formal_action_readiness": {
                        "status": "READY",
                        "ready": True,
                        "request_scoped_pit_resolved": True,
                    },
                    "market_quote": {
                        "mode": "QUERY_TIME_IMMEDIATE_REFRESH",
                        "decision_freshness": {
                            "request_time": "2026-09-22T13:54:38+00:00",
                            "resolved_post_request": True,
                        },
                    },
                }
            })
            req = {"source": "CHATGPT_MANUAL_FORMAL_COMPLETION", "parent_request_id": parent_id, "formal_decision": {}}
            with patch.object(refresh_gate, "REQUEST_DIR", request_dir), patch.object(refresh_gate, "QUERY_CONTEXT", query_context):
                self.assertFalse(refresh_gate._manual_completion_request_bound_pit_ready(req))


if __name__ == "__main__":
    unittest.main()
