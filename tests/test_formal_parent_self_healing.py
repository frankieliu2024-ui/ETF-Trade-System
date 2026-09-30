import json
import tempfile
import unittest
from pathlib import Path

from scripts import runtime_self_heal as heal


class FormalParentSelfHealingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.requests = self.root / "requests" / "live_snapshot"
        self.requests.mkdir(parents=True)
        self.original_root = heal.ROOT
        self.original_query = heal.QUERY_CONTEXT_PATH
        heal.ROOT = self.root
        heal.QUERY_CONTEXT_PATH = self.root / "data" / "state" / "query_context.json"

    def tearDown(self):
        heal.ROOT = self.original_root
        heal.QUERY_CONTEXT_PATH = self.original_query
        self.tmp.cleanup()

    def write(self, name, payload):
        path = self.requests / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_unfinished_parent_returns_existing_ingress_path(self):
        path = self.write(
            "formal.json",
            {
                "request_id": "p1",
                "request_type": "MARKET_QUOTE_REFRESH",
                "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
                "requested_at_beijing": "2026-09-30T11:11:00+08:00",
            },
        )
        result = heal.latest_unfinished_formal_parent()
        self.assertEqual(result[0], str(path.relative_to(self.root)))
        self.assertEqual(result[1], "p1")

    def test_same_request_context_or_business_source_stops_replay(self):
        self.write(
            "formal.json",
            {
                "request_id": "p1",
                "request_type": "MARKET_QUOTE_REFRESH",
                "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
                "requested_at_beijing": "2026-09-30T11:11:00+08:00",
            },
        )
        heal.QUERY_CONTEXT_PATH.parent.mkdir(parents=True)
        heal.QUERY_CONTEXT_PATH.write_text(
            json.dumps({"decision_fact_pack": {"trigger": {"request_id": "p1"}}}),
            encoding="utf-8",
        )
        self.assertEqual(heal.latest_unfinished_formal_parent(), (None, None))

        heal.QUERY_CONTEXT_PATH.write_text("{}", encoding="utf-8")
        self.write(
            "bds.json",
            {
                "request_type": "BUSINESS_DECISION_SOURCE",
                "parent_request_id": "p1",
            },
        )
        self.assertEqual(heal.latest_unfinished_formal_parent(), (None, None))

    def test_workflow_reuses_single_canonical_replay_input(self):
        workflow = Path(".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("formal_replay_request", workflow)
        self.assertIn("market-snapshot.yml", workflow)
        self.assertNotIn("formal-decision-recovery.yml", workflow)
        source = Path("scripts/runtime_self_heal.py").read_text(encoding="utf-8")
        self.assertIn('"REPLAY_FORMAL_REQUEST"', source)
        self.assertIn("latest_unfinished_formal_parent", source)


if __name__ == "__main__":
    unittest.main()
