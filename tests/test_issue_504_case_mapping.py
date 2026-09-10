import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import process_state_sync_request as sync
from scripts import check_system_consistency as consistency


class Issue504CaseMappingTests(unittest.TestCase):
    def test_existing_cases_use_current_trade_event_and_authoritative_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "events/reviews"
            trades = root / "events/trades"
            reviews.mkdir(parents=True)
            trades.mkdir(parents=True)
            (reviews / "2026-09-09.json").write_text(json.dumps({
                "review": {"case_mapping": {"primary": {
                    "trade_event_id": "old-buy", "case_id": "CASE-20260909-01",
                    "security_code": "515220"
                }}}
            }), encoding="utf-8")
            for event_id, code, decision in [
                ("trade_20260910_133720_515220_sell_3700", "515220", "decision-515220"),
                ("trade_20260910_143741_159326_sell_3000", "159326", "authoritative-159326"),
            ]:
                (trades / (event_id + ".json")).write_text(json.dumps({
                    "event_id": event_id, "code": code, "side": "SELL",
                    "execution_status": "EXECUTED",
                    "confirmed_at_beijing": "2026-09-10T14:00:00+08:00",
                    "linked_decision_id": decision
                }), encoding="utf-8")
            (reviews / "2026-09-08.json").write_text(json.dumps({
                "review": {"case_mapping": {"primary": {
                    "trade_event_id": "old-159326", "case_id": "CASE-20260902-01",
                    "security_code": "159326"
                }}}
            }), encoding="utf-8")
            with patch.object(sync, "ROOT", root), patch.object(sync, "load_json", lambda p: json.loads(Path(p).read_text(encoding="utf-8"))):
                result = sync._normalize_executed_trade_case_mapping({"case_mapping": {}}, "2026-09-10")
            updates = {x["trade_event_id"]: x for x in result["case_mapping"]["existing_case_updates"]}
            self.assertEqual(updates["trade_20260910_133720_515220_sell_3700"]["case_id"], "CASE-20260909-01")
            self.assertEqual(updates["trade_20260910_143741_159326_sell_3000"]["decision_id"], "authoritative-159326")

    def test_first_seen_exit_is_explicitly_ineligible_not_new_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events/trades").mkdir(parents=True)
            event_id = "trade_20260910_133746_301689_sell_500"
            (root / "events/trades" / (event_id + ".json")).write_text(json.dumps({
                "event_id": event_id, "code": "301689", "side": "SELL",
                "execution_status": "EXECUTED",
                "confirmed_at_beijing": "2026-09-10T13:37:46+08:00",
                "linked_decision_id": "ipo-exit-decision"
            }), encoding="utf-8")
            with patch.object(sync, "ROOT", root), patch.object(sync, "load_json", lambda p: json.loads(Path(p).read_text(encoding="utf-8"))):
                result = sync._normalize_executed_trade_case_mapping({"case_mapping": {}}, "2026-09-10")
            item = result["case_mapping"]["ineligible_executed_trades"][0]
            self.assertEqual(item["trade_event_id"], event_id)
            self.assertEqual(item["eligibility"], "EXPLICIT_CANONICAL_INELIGIBILITY")
            self.assertEqual(result["case_mapping"]["existing_case_updates"], [])

    def test_checker_accepts_explicit_ineligibility_only_as_canonical_review_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "events/reviews"
            reviews.mkdir(parents=True)
            (reviews / "2026-09-10.json").write_text(json.dumps({
                "review": {"case_mapping": {"ineligible_executed_trades": [{
                    "trade_event_id": "trade-301689",
                    "eligibility": "EXPLICIT_CANONICAL_INELIGIBILITY"
                }]}}
            }), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                self.assertIn("trade-301689", consistency._canonical_case_ineligibilities())


if __name__ == "__main__":
    unittest.main()
