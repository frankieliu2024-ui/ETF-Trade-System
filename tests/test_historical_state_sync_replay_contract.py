import json
import pathlib
import unittest


class HistoricalReplayDispatchContractTests(unittest.TestCase):
    def test_existing_workflow_exposes_single_replay_request_input(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("replay_request:"), 1)
        self.assertIn("requests/live_snapshot", workflow)
        self.assertIn("STATE_SYNC_ONLY", workflow)
        self.assertIn("process_state_sync_request.py", workflow)

    def test_all_canonical_trade_requests_have_admissible_identity(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        pairs = [
            ("requests/live_snapshot/20260910_1340_user_confirmed_dual_sell.json",
             "events/trades/trade_20260910_133720_515220_sell_3700.json"),
            ("requests/live_snapshot/20260910_1341_user_confirmed_301689_sell.json",
             "events/trades/trade_20260910_133746_301689_sell_500.json"),
            ("requests/live_snapshot/20260910_160300_159326_sell_account_closure.json",
             "events/trades/trade_20260910_143741_159326_sell_3000.json"),
        ]
        for request_name, event_name in pairs:
            request = json.loads(pathlib.Path(request_name).read_text(encoding="utf-8"))
            event = json.loads(pathlib.Path(event_name).read_text(encoding="utf-8"))
            trade = request["trade_event"]
            self.assertEqual(request["request_type"], "STATE_SYNC_ONLY")
            self.assertEqual(trade["event_id"], event["event_id"])
            self.assertEqual(trade["code"], event["code"])
            self.assertEqual(trade["side"], event["side"])
            self.assertEqual(float(trade["quantity"]), float(event["quantity"]))
            self.assertEqual(float(trade["price"]), float(event["price"]))
            self.assertEqual(event["execution_status"], "EXECUTED")
        legacy = json.loads(pathlib.Path(pairs[0][0]).read_text(encoding="utf-8"))
        self.assertNotIn("execution_status", legacy["trade_event"])
        self.assertIn("canonical_path = Path(\"events/trades\")", workflow)
        self.assertIn("canonical trade event is missing", workflow)
        self.assertIn("canonical trade event is not EXECUTED", workflow)
        self.assertIn("canonical trade event mismatch", workflow)
        self.assertIn("asset classification", workflow)

    def test_replay_rejects_missing_or_non_executed_canonical_event(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("embedded trade_event is not EXECUTED", workflow)
        self.assertIn("trade_event.event_id is invalid", workflow)
        self.assertIn("same semantic time field", workflow)


if __name__ == "__main__":
    unittest.main()
