import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_e2e_status
import process_state_sync_request as sync


class PostCloseReviewCanonicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "events/reviews").mkdir(parents=True)
        (self.root / "data/state").mkdir(parents=True)
        (self.root / "ETF市场行情档案_2026.md").write_text(
            "## 6. 历史Excel与专项数据来源\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->\n", encoding="utf-8"
        )
        (self.root / "ETF交易复盘与经验库_2026.md").write_text(
            "## 3. 历史研究与专项回测\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->\n", encoding="utf-8"
        )
        (self.root / "data/state/CURRENT.json").write_text("{}\n", encoding="utf-8")
        self.old_root = sync.ROOT
        self.old_paths = (sync.ARCHIVE, sync.EXPERIENCE, sync.ACCOUNT)
        sync.ROOT = self.root
        sync.ARCHIVE = self.root / "ETF市场行情档案_2026.md"
        sync.EXPERIENCE = self.root / "ETF交易复盘与经验库_2026.md"
        sync.ACCOUNT = self.root / "data/state/account_fact.json"

    def tearDown(self):
        sync.ROOT = self.old_root
        sync.ARCHIVE, sync.EXPERIENCE, sync.ACCOUNT = self.old_paths
        self.tmp.cleanup()

    def request(self, reviewed_at="2026-08-31T15:20:00+08:00"):
        return {
            "request_id": "review-20260831",
            "interaction_scenario": "POST_CLOSE_REVIEW",
            "requested_at_beijing": reviewed_at,
            "formal_review": {
                "market_date": "2026-08-31",
                "reviewed_at_beijing": reviewed_at,
                "case_id": "CASE-20260827-01",
                "case_mode": "CONTINUATION_NO_NEW_CASE",
                "data_time": {"close_snapshot": "data/market/snapshots/2026-08-31_150110.json"},
                "etf_strategy_known_net": {
                    "known_net_strategy_equity": 185816.89,
                    "known_net_cumulative_pnl": -14183.11,
                    "etf_strategy_risk_rate_pct": -7.0916,
                },
                "archive_entry": "2026-08-31｜正式收盘事实归档。",
                "experience_entry": "2026-08-31｜延续既有Trial假设，不新增CASE。",
            },
        }

    def test_review_writes_closure_and_same_payload_is_idempotent(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00"}
        self.assertEqual(sync.record_post_close_review(account, self.request()), (True, False))
        event_path = self.root / "events/reviews/2026-08-31.json"
        closure_path = self.root / "data/state/close_review_closure_2026-08-31.json"
        self.assertTrue(event_path.exists())
        self.assertEqual(json.loads(closure_path.read_text(encoding="utf-8"))["status"], "CLOSED")
        self.assertEqual(sync.record_post_close_review(account, self.request()), (True, True))
        self.assertEqual(len(list((self.root / "events/reviews").glob("*.json"))), 1)

    def test_late_older_review_cannot_replace_newer_review(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00"}
        self.assertEqual(sync.record_post_close_review(account, self.request("2026-08-31T15:30:00+08:00")), (True, False))
        self.assertEqual(sync.record_post_close_review(account, self.request("2026-08-31T15:20:00+08:00")), (True, True))
        event = json.loads((self.root / "events/reviews/2026-08-31.json").read_text(encoding="utf-8"))
        self.assertEqual(event["reviewed_at_beijing"], "2026-08-31T15:30:00+08:00")

    def test_e2e_blocks_ready_close_without_canonical_review(self):
        event_path = self.root / "post_market_review/post_market_review_event.json"
        event_path.parent.mkdir(parents=True)
        event_path.write_text(json.dumps({"market_close": True, "market_date": "2026-08-31", "status": "READY_FOR_REVIEW"}), encoding="utf-8")
        old = (build_e2e_status.POST_MARKET_REVIEW, build_e2e_status.REVIEW_DIR, build_e2e_status.STATE)
        build_e2e_status.POST_MARKET_REVIEW = event_path
        build_e2e_status.REVIEW_DIR = self.root / "events/reviews"
        build_e2e_status.STATE = self.root / "data/state"
        try:
            result = build_e2e_status.close_review_component({"market_date": "2026-08-31"})
        finally:
            build_e2e_status.POST_MARKET_REVIEW, build_e2e_status.REVIEW_DIR, build_e2e_status.STATE = old
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "FORMAL_POST_CLOSE_REVIEW_NOT_CANONICALIZED")


if __name__ == "__main__":
    unittest.main()
