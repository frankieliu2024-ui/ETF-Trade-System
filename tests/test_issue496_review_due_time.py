import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import build_e2e_status as e2e
from scripts import check_system_consistency as consistency
from scripts.post_close_review_due import configured_review_due_time, review_due_state


def t(value):
    return datetime.fromisoformat(value)


class ReviewDueTimeTests(unittest.TestCase):
    def test_due_boundaries_are_runtime_configurable(self):
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", t("2026-09-10T19:29:00+08:00"), "19:30"), "NOT_DUE_TODAY")
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", t("2026-09-10T19:30:00+08:00"), "19:30"), "DUE_OR_OVERDUE")
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", t("2026-09-10T20:29:00+08:00"), "20:30"), "NOT_DUE_TODAY")
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", t("2026-09-10T20:30:00+08:00"), "20:30"), "DUE_OR_OVERDUE")
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", t("2026-09-10T20:59:00+08:00"), "21:00"), "NOT_DUE_TODAY")
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", t("2026-09-10T21:00:00+08:00"), "21:00"), "DUE_OR_OVERDUE")
        self.assertEqual(review_due_state("2026-09-09", "2026-09-10", t("2026-09-10T16:00:00+08:00"), "21:00"), "PRIOR_DAY_OVERDUE")

    def test_missing_or_invalid_schedule_is_fail_safe(self):
        self.assertIsNone(configured_review_due_time({}))
        self.assertIsNone(configured_review_due_time({"scheduled_trade_review": {"due_time": "25:00"}}))
        self.assertEqual(review_due_state("2026-09-10", "2026-09-10", t("2026-09-10T16:00:00+08:00"), None), "INVALID_CONFIG")

    def test_consistency_pre_due_pending_and_post_due_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "post_market_review").mkdir()
            (root / "data/state").mkdir(parents=True)
            (root / "config").mkdir()
            event = {"market_close": True, "status": "READY_FOR_REVIEW", "market_date": "2026-09-10"}
            (root / "post_market_review/post_market_review_event.json").write_text(json.dumps(event), encoding="utf-8")
            (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-09-10"}), encoding="utf-8")
            (root / "config/runtime_policy.json").write_text(json.dumps({"scheduled_trade_review": {"due_time": "20:30"}}), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_post_close_review_contract(report, t("2026-09-10T16:00:00+08:00"))
                self.assertEqual(report["errors"], [])
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_post_close_review_contract(report, t("2026-09-10T20:30:00+08:00"))
                self.assertTrue(report["errors"])

    def test_e2e_pre_due_and_post_due_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "post_market_review").mkdir()
            (root / "events/reviews").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "post_market_review/post_market_review_event.json").write_text(json.dumps({"market_close": True, "status": "READY_FOR_REVIEW", "market_date": "2026-09-10"}), encoding="utf-8")
            (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-09-10"}), encoding="utf-8")
            (root / "config/runtime_policy.json").write_text(json.dumps({"scheduled_trade_review": {"due_time": "21:00"}}), encoding="utf-8")
            with patch.object(e2e, "ROOT", root), patch.object(e2e, "POST_MARKET_REVIEW", root / "post_market_review/post_market_review_event.json"), patch.object(e2e, "REVIEW_DIR", root / "events/reviews"), patch.object(e2e, "STATE", root / "data/state"):
                self.assertEqual(e2e.close_review_component({"market_date": "2026-09-10"}, t("2026-09-10T20:59:00+08:00"))["status"], "READY")
                self.assertEqual(e2e.close_review_component({"market_date": "2026-09-10"}, t("2026-09-10T21:00:00+08:00"))["status"], "BLOCKED")

    def test_prior_day_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "post_market_review").mkdir()
            (root / "events/reviews").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "post_market_review/post_market_review_event.json").write_text(json.dumps({"market_close": True, "status": "READY_FOR_REVIEW", "market_date": "2026-09-09"}), encoding="utf-8")
            (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-09-10"}), encoding="utf-8")
            (root / "config/runtime_policy.json").write_text(json.dumps({"scheduled_trade_review": {"due_time": "21:00"}}), encoding="utf-8")
            with patch.object(e2e, "ROOT", root), patch.object(e2e, "POST_MARKET_REVIEW", root / "post_market_review/post_market_review_event.json"), patch.object(e2e, "REVIEW_DIR", root / "events/reviews"), patch.object(e2e, "STATE", root / "data/state"):
                self.assertEqual(e2e.close_review_component({"market_date": "2026-09-10"}, t("2026-09-10T16:00:00+08:00"))["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
