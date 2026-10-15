from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import process_state_sync_request as sync


class FormalDecisionBdsContractTests(unittest.TestCase):
    def test_completion_rejects_producer_payload_without_request_type(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bds_dir = root / "requests" / "live_snapshot"
            bds_dir.mkdir(parents=True)
            body = "唯一冻结的 Formal Decision 正文"
            parent_id = "formal-parent-20261016-100000-ab12cd34"
            decision_id = "fd-20261016-1000-automatic-ab12cd34_decision"
            bds_name = parent_id + "__business_decision_source.json"
            bds = {
                "request_id": parent_id,
                "parent_request_id": parent_id,
                "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
                "occurrence_id": "occurrence-20261016-100000-ab12cd34",
                "market_date": "2026-10-16",
                "decision_id": decision_id,
                "decision_response": {"answers": {"RISK_PERMISSION": {"status": "ANSWERED"}}},
                "presentation_content": body,
                "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
            completion = {
                "request_id": parent_id + "__formal_decision_completion",
                "parent_request_id": parent_id,
                "request_type": "STATE_SYNC_ONLY",
                "source": "CHATGPT_FORMAL_DECISION_COMPLETION",
                "formal_fact_type": "FORMAL_DECISION_COMPLETION",
                "bds_path": "requests/live_snapshot/" + bds_name,
                "decision_id": decision_id,
                "content_hash": bds["content_hash"],
            }
            bds_path = bds_dir / bds_name
            completion_path = bds_dir / (completion["request_id"] + ".json")
            bds_path.write_text(json.dumps(bds), encoding="utf-8")
            completion_path.write_text(json.dumps(completion), encoding="utf-8")
            with mock.patch.object(sync, "ROOT", root), mock.patch.object(
                sys, "argv", ["process_state_sync_request.py", str(completion_path.relative_to(root))]
            ):
                with self.assertRaisesRegex(
                    ValueError, "must reference BUSINESS_DECISION_SOURCE"
                ):
                    sync.main()


if __name__ == "__main__":
    unittest.main()
