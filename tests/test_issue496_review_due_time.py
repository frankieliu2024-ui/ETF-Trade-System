import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import build_e2e_status as e2e
from scripts import check_system_consistency as consistency
from scripts.post_close_review_due import review_due_state


class ReviewDueTimeTests(unittest.TestCase):
    def test_due_state_boundaries_and_prior_day(self):
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", datetime.fromisoformat("2026-09-10T20:29:00+08:00")), "NOT_DUE_TODAY")
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", datetime.fromisoformat("2026-09-10T20:30:00+08:00")), "DUE_OR_OVERDUE")
        self.assertEqual(review_due_state("2026-09-09", "2026-09-10", datetime.fromisoformat("2026-09-10T16:00:00+08:00")), "PRIOR_DAY_OVERDUE")

    def test_consistency_pre_due_pending_and_post_due_failure_use_temp_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "post_market_review").mkdir()
            (root / "data/state").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "post_market_review/post_market_review_event.json").write_text(json.dumps({"market_close": True, "status": "READY_FOR_REVIEW", "market_date": "2026-09-10"}), encoding="utf-8")
            (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-09-10"}), encoding="utf-8")
            (root / "config/runtime_policy.json").write_text(json.dumps({"scheduled_trade_review": {"due_time": "20:30"}}), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_post_close_review_contract(report, datetime.fromisoformat("2026-09-10T16:00:00+08:00"))
                self.assertEqual(report["errors"], [])
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_post_close_review_contract(report, datetime.fromisoformat("2026-09-10T20:30:00+08:00"))
                self.assertTrue(report["errors"])

    def test_e2e_pre_due_is_ready_and_post_due_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "post_market_review").mkdir()
            (root / "events/reviews").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            (root / "post_market_review/post_market_review_event.json").write_text(json.dumps({"market_close": True, "status": "READY_FOR_REVIEW", "market_date": "2026-09-10"}), encoding="utf-8")
            with patch.object(e2e, "ROOT", root), patch.object(e2e, "POST_MARKET_REVIEW", root / "post_market_review/post_market_review_event.json"), patch.object(e2e, "REVIEW_DIR", root / "events/reviews"), patch.object(e2e, "STATE", root / "data/state"):
                self.assertEqual(e2e.close_review_component({"market_date": "2026-09-10"}, datetime.fromisoformat("2026-09-10T16:00:00+08:00"))["status"], "READY")
                self.assertEqual(e2e.close_review_component({"market_date": "2026-09-10"}, datetime.fromisoformat("2026-09-10T20:31:00+08:00"))["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
