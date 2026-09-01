import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_system_consistency as consistency


class ExecutedTradeCaseLifecycleTests(unittest.TestCase):
    def test_same_day_intraday_pending_case_is_allowed(self):
        current = {"market_date": "2026-09-01", "latest_valid_node": "live", "data_freshness": {"market_phase": "CONTINUOUS_AFTERNOON"}}
        event = {"event_id": "trade-1", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}
        self.assertFalse(consistency._case_mapping_required(current, event))

    def test_post_close_requires_final_case_mapping(self):
        current = {"market_date": "2026-09-01", "latest_valid_node": "close", "data_freshness": {"market_phase": "POST_CLOSE_GRACE"}}
        event = {"event_id": "trade-1", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}
        self.assertTrue(consistency._case_mapping_required(current, event))

    def test_old_trade_requires_final_case_mapping_on_new_day(self):
        current = {"market_date": "2026-09-02", "latest_valid_node": "live", "data_freshness": {"market_phase": "CONTINUOUS_MORNING"}}
        event = {"event_id": "trade-1", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}
        self.assertTrue(consistency._case_mapping_required(current, event))

    def test_checker_accepts_pending_intraday_but_fails_post_close_until_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ETF市场行情档案_2026.md").write_text("trade-1｜archive\n", encoding="utf-8")
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text("trade-1｜- 待复盘CASE｜2026-09-01T14:40:01+08:00｜\nTRADE_EVENT:trade-1\n", encoding="utf-8")
            trade_dir = root / "events" / "trades"
            trade_dir.mkdir(parents=True)
            (trade_dir / "trade-1.json").write_text(json.dumps({"event_id": "trade-1", "execution_status": "EXECUTED", "confirmed_at_beijing": "2026-09-01T14:40:01+08:00"}), encoding="utf-8")
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            current_path = state_dir / "CURRENT.json"
            current_path.write_text(json.dumps({"market_date": "2026-09-01", "latest_valid_node": "live", "data_freshness": {"market_phase": "CONTINUOUS_AFTERNOON"}}), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_trade_event_formal_sync(report)
                self.assertEqual(report["checks"][-1]["status"], "PASS")
                current_path.write_text(json.dumps({"market_date": "2026-09-01", "latest_valid_node": "close", "data_freshness": {"market_phase": "POST_CLOSE_GRACE"}}), encoding="utf-8")
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_trade_event_formal_sync(report)
                self.assertEqual(report["checks"][-1]["status"], "FAIL")
                self.assertIn("trade-1:formal_case_mapping", report["errors"])
                experience.write_text("### CASE-20260901-01：review\ntrade-1｜- 已归入CASE-20260901-01｜2026-09-01T15:10:00+08:00｜\nTRADE_EVENT:trade-1\n", encoding="utf-8")
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_trade_event_formal_sync(report)
                self.assertEqual(report["checks"][-1]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
