import hashlib
import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from scripts.formal_entry_contract import (
    ENTRY_AUTOMATIC_SCHEDULED_TASK,
    ENTRY_MANUAL_CONVERSATION,
    ENTRY_MANUAL_SCHEDULED_TASK,
    build_identity,
)


class FormalDecisionE2EContractTests(unittest.TestCase):
    def test_three_entry_identities_are_distinct_and_valid(self):
        cases = [
            (ENTRY_MANUAL_CONVERSATION, "CHATGPT_MANUAL_FORMAL_ANALYSIS", "manual"),
            (ENTRY_MANUAL_SCHEDULED_TASK, "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", "manual_task"),
            (ENTRY_AUTOMATIC_SCHEDULED_TASK, "CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "scheduled_20261013_1430"),
        ]
        identities = []
        for mode, source, occurrence in cases:
            payload = {
                "request_id": "fd_" + occurrence,
                "source": source,
                "entry_mode": mode,
                "occurrence_id": occurrence,
                "task_id": "ETF正式决策",
                "task_run_id": "run_" + occurrence,
                "occurrence": occurrence,
                "market_date": "2026-10-13",
            }
            identity = build_identity(payload)
            identities.append(identity["dedup_key"])
        self.assertEqual(len(identities), len(set(identities)))

    def test_formal_report_requires_exact_current_decision_event(self):
        import scripts.notification_center as center

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "events" / "decisions"
            decisions.mkdir(parents=True)
            body = "【ETF正式决策】当前请求正式报告"
            event = {
                "event_type": "FORMAL_DECISION",
                "parent_request_id": "fd_current",
                "decision_id": "decision_current",
                "formal_decision": {},
                "presentation_binding": {
                    "request_id": "fd_current",
                    "decision_id": "decision_current",
                    "content": body,
                    "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                },
            }
            path = decisions / "current.json"
            path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
            with patch.object(center, "ROOT", root), patch.dict(os.environ, {"DECISION_EVENT_PATH": "events/decisions/current.json"}):
                self.assertIsNotNone(center.formal_decision_report_event())
            with patch.object(center, "ROOT", root), patch.dict(os.environ, {"DECISION_EVENT_PATH": "events/decisions/missing.json"}):
                self.assertIsNone(center.formal_decision_report_event())

    def test_unbound_formal_notification_fails_closed(self):
        import scripts.notification_center as center

        with patch.dict(os.environ, {"DECISION_EVENT_PATH": ""}, clear=False):
            self.assertIsNone(center.formal_decision_report_event())


if __name__ == "__main__":
    unittest.main()
