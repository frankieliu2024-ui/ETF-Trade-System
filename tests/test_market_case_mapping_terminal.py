import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_system_consistency import _valid_unrecoverable_review_terminal


class TerminalReviewCaseMappingTest(unittest.TestCase):
    def test_unrecoverable_review_is_not_a_missing_case_error(self):
        event_path = ROOT / "events/trades/20260901_143932_561980_reduce_risk_execution.json"
        event = json.loads(event_path.read_text(encoding="utf-8"))
        self.assertTrue(_valid_unrecoverable_review_terminal(event["event_id"], event))


if __name__ == "__main__":
    unittest.main()
