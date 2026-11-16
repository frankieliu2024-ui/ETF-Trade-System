import json
import tempfile
import unittest
from pathlib import Path

from scripts.sync_formal_files import reconcile_case_detail_projections


class FormalCaseProjectionReconciliationTests(unittest.TestCase):
    def test_rebuilds_missing_heading_from_canonical_mapping_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events" / "reviews").mkdir(parents=True)
            (root / "events" / "trades").mkdir()
            event_id = "broker-trade-20261113-095227-159529"
            (root / "events" / "trades" / "event.json").write_text(
                json.dumps({
                    "event_id": event_id,
                    "name": "标普消费ETF",
                    "code": "159529",
                    "side": "BUY",
                    "quantity": 1000,
                    "price": 1.234,
                    "confirmed_at_beijing": "2026-11-13T09:52:27+08:00",
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "events" / "reviews" / "review.json").write_text(
                json.dumps({"review": {"case_mapping": {"existing_case_updates": [{
                    "trade_event_id": event_id,
                    "case_id": "CASE-20261113-01",
                    "security_code": "159529",
                    "mapping_reason": "canonical mapping",
                }]}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            text = (
                "# ETF交易复盘与经验库_2026\n\n"
                "<!-- AUTO_CASE_DETAILS_START -->\n"
                "<!-- AUTO_CASE_DETAILS_END -->\n\n"
                "## 3. 历史研究与专项回测\n"
            )
            updated, repaired = reconcile_case_detail_projections(root, text)
            self.assertEqual(repaired, 1)
            self.assertIn("### 2.3 CASE-20261113-01：标普消费ETF（159529）成交闭环登记", updated)
            self.assertIn(event_id, updated)
            self.assertEqual(updated.count("CASE-20261113-01"), 1)

            unchanged, repaired_again = reconcile_case_detail_projections(root, updated)
            self.assertEqual(repaired_again, 0)
            self.assertEqual(unchanged, updated)


if __name__ == "__main__":
    unittest.main()
