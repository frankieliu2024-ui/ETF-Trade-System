import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import process_state_sync_request as sync


class Issue598MiddayReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "events/reviews").mkdir(parents=True)
        (self.root / "data/state").mkdir(parents=True)
        (self.root / "ETF市场行情档案_2026.md").write_text(
            "## 6. 历史Excel与专项数据来源\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->\n",
            encoding="utf-8",
        )
        (self.root / "ETF交易复盘与经验库_2026.md").write_text(
            "## 3. 历史研究与专项回测\n"
            "<!-- AUTO_CASE_DETAILS_START -->\n"
            "<!-- AUTO_CASE_DETAILS_END -->\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->\n",
            encoding="utf-8",
        )
        (self.root / "data/state/CURRENT.json").write_text("{}\n", encoding="utf-8")
        self.old = (sync.ROOT, sync.ARCHIVE, sync.EXPERIENCE, sync.ACCOUNT)
        sync.ROOT = self.root
        sync.ARCHIVE = self.root / "ETF市场行情档案_2026.md"
        sync.EXPERIENCE = self.root / "ETF交易复盘与经验库_2026.md"
        sync.ACCOUNT = self.root / "data/state/account_fact.json"
        self.account = {
            "status": "VALID",
            "updated_at": "2026-09-15T12:25:00+08:00",
            "last_confirmed_market_date": "2026-09-15",
            "positions": [],
        }

    def tearDown(self):
        sync.ROOT, sync.ARCHIVE, sync.EXPERIENCE, sync.ACCOUNT = self.old
        self.tmp.cleanup()

    def _request(self, *, scope, reviewed_at, close_snapshot, verified_close):
        return {
            "request_id": f"review-{scope}-{reviewed_at}",
            "interaction_scenario": "POST_CLOSE_REVIEW",
            "requested_at_beijing": reviewed_at,
            "formal_review": {
                "market_date": "2026-09-15",
                "reviewed_at_beijing": reviewed_at,
                "review_scope": scope,
                "lifecycle": {},
                "holding_actions": {},
                "data_time": {
                    "close_snapshot": close_snapshot,
                    "close_data_contract": {
                        "status": "VERIFIED_SESSION_CLOSE" if verified_close else "UNVERIFIED",
                        "verified_session_close": verified_close,
                        "market_date": "2026-09-15",
                        "effective_market_time_beijing": "2026-09-15T15:00:00+08:00" if verified_close else "",
                        "snapshot_node": "close" if verified_close else "1130",
                        "snapshot_planned_time": "15:00" if verified_close else "11:30",
                    },
                },
            },
        }

    def test_midday_stage_cannot_write_formal_post_close_event_or_closure(self):
        request = self._request(
            scope="MORNING_SESSION_STAGE_ONLY",
            reviewed_at="2026-09-15T12:32:00+08:00",
            close_snapshot="data/market/snapshots/2026-09-15_113432.json",
            verified_close=False,
        )
        self.assertEqual(sync.record_post_close_review(self.account, request), (False, False))
        self.assertFalse((self.root / "events/reviews/2026-09-15.json").exists())
        self.assertFalse((self.root / "data/state/close_review_closure_2026-09-15.json").exists())

    def test_unverified_close_cannot_write_even_without_stage_scope(self):
        request = self._request(
            scope="FULL_DAY",
            reviewed_at="2026-09-15T12:40:00+08:00",
            close_snapshot="data/market/snapshots/2026-09-15_113432.json",
            verified_close=False,
        )
        self.assertEqual(sync.record_post_close_review(self.account, request), (False, False))
        self.assertFalse((self.root / "events/reviews/2026-09-15.json").exists())

    def test_same_date_full_day_verified_close_remains_eligible_after_midday_rejection(self):
        midday = self._request(
            scope="MORNING_SESSION_STAGE_ONLY",
            reviewed_at="2026-09-15T12:32:00+08:00",
            close_snapshot="data/market/snapshots/2026-09-15_113432.json",
            verified_close=False,
        )
        self.assertEqual(sync.record_post_close_review(self.account, midday), (False, False))

        full_day = self._request(
            scope="FULL_DAY",
            reviewed_at="2026-09-15T20:30:00+08:00",
            close_snapshot="data/market/snapshots/2026-09-15_151500.json",
            verified_close=True,
        )
        self.assertEqual(sync.record_post_close_review(self.account, full_day), (True, False))
        event = json.loads((self.root / "events/reviews/2026-09-15.json").read_text(encoding="utf-8"))
        self.assertEqual(event["event_type"], "FORMAL_POST_CLOSE_REVIEW")
        closure = json.loads((self.root / "data/state/close_review_closure_2026-09-15.json").read_text(encoding="utf-8"))
        self.assertEqual(closure["status"], "CLOSED")
        self.assertIn("151500", closure["close_snapshot"])


if __name__ == "__main__":
    unittest.main()
