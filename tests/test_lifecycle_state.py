from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.lifecycle_state import build_lifecycle_projection


def write(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class LifecycleStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write(self.root, "config/market/a_share_trading_calendar_2026.json", {"closed_dates": []})
        write(self.root, "data/state/CURRENT.json", {"market_date": "2026-09-01"})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_trial(self, decision_date="2026-08-27", trade_date="2026-08-27", hypothesis="H1") -> None:
        write(self.root, f"events/decisions/{hypothesis}.json", {
            "decision_id": hypothesis, "decision_time_beijing": f"{decision_date}T09:56:49+08:00",
            "candidate_code": "515880", "candidate_name": "通信ETF", "hypothesis_id": hypothesis,
            "hypothesis_closed": False, "formal_decision": {"lifecycle": "通信ETF（515880）：Trial"},
        })
        write(self.root, f"events/trades/{hypothesis}.json", {
            "event_id": f"trade-{hypothesis}", "linked_decision_id": hypothesis,
            "hypothesis_id": hypothesis, "execution_status": "EXECUTED", "execution_date": trade_date,
            "code": "515880", "name": "通信ETF",
        })

    def test_515880_is_t3_on_2026_09_01_and_weekend_is_not_counted(self):
        self.add_trial()
        result = build_lifecycle_projection(self.root)
        item = result["active_lifecycles"][0]
        self.assertEqual(item["current_t_plus"], 3)
        self.assertEqual(item["mandatory_decision_market_date"], "2026-09-01")
        self.assertTrue(item["decision_due"])
        self.assertEqual(build_lifecycle_projection(self.root, "2026-08-31")["active_lifecycles"][0]["current_t_plus"], 2)

    def test_decision_without_execution_does_not_start_real_lifecycle(self):
        write(self.root, "events/decisions/no-trade.json", {
            "decision_id": "no-trade", "decision_time_beijing": "2026-08-27T09:00:00+08:00",
            "candidate_code": "515880", "hypothesis_id": "H-no-trade",
            "formal_decision": {"lifecycle": "通信ETF（515880）：Trial"},
        })
        self.assertEqual(build_lifecycle_projection(self.root)["active_lifecycles"], [])

    def test_t3_resolution_is_not_due_again(self):
        self.add_trial()
        write(self.root, "events/decisions/confirm.json", {
            "decision_id": "confirm", "decision_time_beijing": "2026-08-31T10:00:00+08:00",
            "hypothesis_id": "H1", "hypothesis_closed": False,
            "formal_decision": {"lifecycle": "通信ETF（515880）：Confirm评估"},
        })
        item = build_lifecycle_projection(self.root)["active_lifecycles"][0]
        self.assertEqual(item["lifecycle_status"], "RESOLVED")
        self.assertFalse(item["decision_due"])

    def test_projection_survives_rebuild_and_exposes_sources(self):
        self.add_trial()
        result = build_lifecycle_projection(self.root, "2026-08-28")
        item = result["active_lifecycles"][0]
        self.assertEqual(item["source_decision_id"], "H1")
        self.assertEqual(item["source_trade_event_id"], "trade-H1")
        self.assertEqual(item["current_t_plus"], 1)
        self.assertEqual(result["next_mandatory_node"], "")


if __name__ == "__main__":
    unittest.main()
