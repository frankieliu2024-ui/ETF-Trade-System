import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import process_state_sync_request as state_sync


class FormalDecisionCanonicalHashOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "events/reviews").mkdir(parents=True)
        self.patch = mock.patch.object(state_sync, "ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def bds(self, body):
        return {
            "request_id": "completion-1",
            "parent_request_id": "parent-1",
            "decision_id": "decision-1",
            "task_id": "ETF正式决策",
            "task_run_id": "run-1",
            "occurrence_id": "occ-1",
            "requested_at_beijing": "2026-10-17T00:00:00+08:00",
            "presentation_content": body,
            "presentation_binding": {
                "request_id": "completion-1",
                "parent_request_id": "parent-1",
                "decision_id": "decision-1",
                "full_content": body,
            },
        }

    def test_owner_derives_missing_hash_from_frozen_content(self):
        body = "frozen formal decision"
        ok, replay, path = state_sync.record_formal_decision_report_event(
            {"parent_request_id": "parent-1"}, self.bds(body)
        )
        self.assertEqual((ok, replay), (True, False))
        event = json.loads((self.root / path).read_text(encoding="utf-8"))
        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.assertEqual(event["presentation_binding"]["content_hash"], expected)
        self.assertEqual(event["presentation_binding"]["content_hash_source"], "canonical_owner")

    def test_owner_rejects_mismatching_supplied_hash(self):
        bds = self.bds("frozen formal decision")
        bds["content_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "content_hash mismatch"):
            state_sync.record_formal_decision_report_event(
                {"parent_request_id": "parent-1"}, bds
            )


if __name__ == "__main__":
    unittest.main()
