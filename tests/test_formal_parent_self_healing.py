import json
import tempfile
import unittest
import os
from pathlib import Path

from scripts import runtime_session_gate

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

    def test_scheduled_formal_parent_is_selected_and_not_shadowed_by_older_manual(self):
        self.write(
            "old.json",
            {
                "request_id": "old",
                "request_type": "MARKET_QUOTE_REFRESH",
                "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
                "requested_at_beijing": "2026-10-12T17:01:11+08:00",
            },
        )
        path = self.write(
            "scheduled.json",
            {
                "request_id": "new",
                "request_type": "MARKET_QUOTE_REFRESH",
                "source": "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
                "requested_at_beijing": "2026-10-12T18:25:33+08:00",
            },
        )
        result = heal.latest_unfinished_formal_parent()
        self.assertEqual(result[0], str(path.relative_to(self.root)))
        self.assertEqual(result[1], "new")

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


    def test_context_recovery_routes_existing_parent_into_canonical_entry(self):
        path = self.write("formal.json", {"request_id":"p1","request_type":"MARKET_QUOTE_REFRESH","source":"CHATGPT_MANUAL_FORMAL_ANALYSIS","intent":"FORMAL_INTRADAY_ANALYSIS","query_intent":"FORMAL_INTRADAY_ANALYSIS","force_refresh":True,"require_post_request_snapshot":True,"requested_at_beijing":"2026-09-30T11:11:00+08:00"})
        old_root = runtime_session_gate.ROOT
        old_event = os.environ.get("GITHUB_EVENT_NAME")
        old_input = os.environ.get("FORMAL_CONTEXT_RECOVERY_REQUEST")
        try:
            runtime_session_gate.ROOT = self.root
            os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
            os.environ["FORMAL_CONTEXT_RECOVERY_REQUEST"] = str(path.relative_to(self.root))
            self.assertEqual(runtime_session_gate._event_request_files(), [path])
            self.assertEqual(runtime_session_gate.classify_live_snapshot_request(json.loads(path.read_text())), "REFRESH_BEARING")
            workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
            self.assertIn("formal_context_recovery_request", workflow)
            self.assertIn("REQUEST_FILE=\"$FORMAL_CONTEXT_RECOVERY_REQUEST\"", workflow)
            self.assertIn("python scripts/build_query_context.py --run-discovery", workflow)
            self.assertIn('[ -n "$FORMAL_CONTEXT_RECOVERY_REQUEST" ]', workflow)
            self.assertNotIn("REPLAY_REQUEST_INPUT", workflow)
            self.assertNotIn("FORMAL_CONTEXT_RECOVERY_REQUEST_INPUT", workflow)
            self.assertNotIn("FORMAL_REPLAY_REQUEST", os.environ)
            self.assertNotIn("BUSINESS_DECISION_SOURCE", json.dumps(json.loads(path.read_text())))
            self.assertEqual(len(list(self.requests.glob("*.json"))), 1)
        finally:
            runtime_session_gate.ROOT = old_root
            if old_event is None: os.environ.pop("GITHUB_EVENT_NAME", None)
            else: os.environ["GITHUB_EVENT_NAME"] = old_event
            if old_input is None: os.environ.pop("FORMAL_CONTEXT_RECOVERY_REQUEST", None)
            else: os.environ["FORMAL_CONTEXT_RECOVERY_REQUEST"] = old_input

    def test_workflow_preserves_normalized_manual_formal_envelope(self):
        workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("normalize_manual_request_session", workflow)
        self.assertIn('dst.write_text(json.dumps(request, ensure_ascii=False, indent=2)', workflow)
        preserve = workflow.index("Preserve triggering request envelope before core publish")
        normalize = workflow.index("normalize_manual_request_session", preserve)
        context = workflow.index("python scripts/build_query_context.py --run-discovery", normalize)
        self.assertLess(normalize, context)

    def test_normalizer_recovers_missing_requested_at_beijing_at_existing_ingress_owner(self):
        policy = json.loads(Path("config/runtime_policy.json").read_text(encoding="utf-8"))
        raw = {
            "request_id": "p-missing-time",
            "request_type": "MARKET_QUOTE_REFRESH",
            "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "intent": "FORMAL_INTRADAY_ANALYSIS",
            "query_intent": "FORMAL_INTRADAY_ANALYSIS",
            "force_refresh": True,
            "require_post_request_snapshot": True,
        }
        from datetime import datetime
        fixed = datetime.fromisoformat("2026-10-11T13:47:48+08:00")
        normalized = runtime_session_gate.normalize_manual_request_session(raw, policy, now=fixed)
        self.assertEqual(normalized["requested_at_beijing"], "2026-10-11T13:47:48+08:00")
        self.assertEqual(normalized["canonical_ingress_received_at_beijing"], "2026-10-11T13:47:48+08:00")
        self.assertEqual(normalized["request_id"], "p-missing-time")

    def test_existing_bds_and_state_sync_routes_are_unchanged(self):
        workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("formal replay requires exactly one durable BUSINESS_DECISION_SOURCE", workflow)
        self.assertIn("REPLAY_REQUEST", workflow)
        self.assertIn("Reject ambiguous recovery inputs", workflow)
        self.assertIn("mutually exclusive", workflow)
        self.assertIn("process_state_sync_request.py", workflow)

    def test_workflow_reuses_single_canonical_replay_input(self):
        workflow = Path(".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("formal_replay_request", workflow)
        self.assertIn("market-snapshot.yml", workflow)
        self.assertIn("-f node=scheduled", workflow)
        self.assertNotIn("-f node=query", workflow)
        self.assertIn("formal_context_recovery_request=", workflow)
        self.assertNotIn("formal-decision-recovery.yml", workflow)
        source = Path("scripts/runtime_self_heal.py").read_text(encoding="utf-8")
        self.assertIn('"REPLAY_FORMAL_REQUEST"', source)
        self.assertIn("latest_unfinished_formal_parent", source)


if __name__ == "__main__":
    unittest.main()
