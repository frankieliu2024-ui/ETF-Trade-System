import pathlib
import unittest


class HistoricalReplayDispatchContractTests(unittest.TestCase):
    def test_existing_workflow_exposes_single_replay_request_input(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("replay_request:"), 1)
        self.assertIn("requests/live_snapshot", workflow)
        self.assertIn("STATE_SYNC_ONLY", workflow)
        self.assertIn("process_state_sync_request.py", workflow)

    def test_legacy_request_uses_canonical_executed_event(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        request = pathlib.Path(
            "requests/live_snapshot/20260910_1340_user_confirmed_dual_sell.json"
        )
        event = pathlib.Path(
            "events/trades/trade_20260910_133720_515220_sell_3700.json"
        )
        self.assertNotIn("execution_status", request.read_text(encoding="utf-8"))
        self.assertIn('"execution_status": "EXECUTED"', event.read_text(encoding="utf-8"))
        self.assertIn("canonical_path = Path(\"events/trades\")", workflow)
        self.assertIn("canonical trade event is missing", workflow)
        self.assertIn("canonical trade event is not EXECUTED", workflow)
        self.assertIn("canonical trade event mismatch", workflow)
        self.assertIn("process_state_sync_request.py", workflow)

    def test_replay_rejects_missing_or_non_executed_canonical_event(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("embedded trade_event is not EXECUTED", workflow)
        self.assertIn("trade_event.event_id is invalid", workflow)
        self.assertIn("time_value(canonical)", workflow)


if __name__ == "__main__":
    unittest.main()
