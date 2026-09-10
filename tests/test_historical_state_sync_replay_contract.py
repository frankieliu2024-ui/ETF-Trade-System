import pathlib
import unittest


class HistoricalReplayDispatchContractTests(unittest.TestCase):
    def test_existing_workflow_exposes_single_replay_request_input(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("replay_request:"), 1)
        self.assertIn("requests/live_snapshot", workflow)
        self.assertIn("STATE_SYNC_ONLY", workflow)
        self.assertIn("process_state_sync_request.py", workflow)

    def test_replay_is_confined_to_existing_request_namespace(self):
        workflow = pathlib.Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("replay_request must be one existing requests/live_snapshot/*.json path", workflow)
        self.assertIn("replay_request must be STATE_SYNC_ONLY", workflow)
        self.assertIn("replay_request must contain an EXECUTED trade_event", workflow)


if __name__ == "__main__":
    unittest.main()
