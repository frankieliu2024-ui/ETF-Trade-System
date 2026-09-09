import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.process_state_sync_request import resolve_trade_linked_decision_id


class TradeLinkedDecisionIdentityTests(unittest.TestCase):
    def test_new_trade_prefers_explicit_linked_decision_id(self):
        linked = resolve_trade_linked_decision_id(
            {"linked_decision_id": "explicit", "decision_id": "legacy"},
            decision_id="formal",
        )
        self.assertEqual(linked, "explicit")

    def test_existing_trade_replay_backfills_missing_explicit_link(self):
        linked = resolve_trade_linked_decision_id(
            {"linked_decision_id": "request-link", "decision_id": "legacy"},
            decision_id="formal",
            existing_linked_decision_id="",
        )
        self.assertEqual(linked, "request-link")

    def test_existing_persisted_link_wins_on_replay(self):
        linked = resolve_trade_linked_decision_id(
            {"linked_decision_id": "request-link", "decision_id": "legacy"},
            decision_id="formal",
            existing_linked_decision_id="persisted",
        )
        self.assertEqual(linked, "persisted")

    def test_legacy_decision_id_and_same_request_formal_decision_remain_compatible(self):
        self.assertEqual(
            resolve_trade_linked_decision_id({"decision_id": "legacy"}, decision_id="formal"),
            "legacy",
        )
        self.assertEqual(
            resolve_trade_linked_decision_id({}, decision_id="formal"),
            "formal",
        )

    def test_missing_all_links_preserves_no_link_behavior(self):
        self.assertEqual(resolve_trade_linked_decision_id({}), "")


if __name__ == "__main__":
    unittest.main()
