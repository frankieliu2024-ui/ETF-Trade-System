import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_formal_files import normalize_dashboard_projection
from process_state_sync_request import latest_formal_review_decision


class DashboardProjectionTests(unittest.TestCase):
    def test_normalize_removes_historical_corrections_and_derives_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calendar = root / "config/market"
            calendar.mkdir(parents=True)
            (calendar / "a_share_trading_calendar_2026.json").write_text(
                json.dumps({"closed_dates": ["2026-09-03"]}), encoding="utf-8"
            )
            text = (
                "top\n### 下一关键节点\nold\n"
                "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->\n"
                "FEE_old｜历史费用\n"
                "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->\n"
            )
            result = normalize_dashboard_projection(
                text, root, {"updated_at": "2026-09-02T21:05:00+08:00"}
            )
            self.assertNotIn("AUTO_TRADE_FACT_CORRECTIONS", result)
            self.assertNotIn("FEE_old", result)
            self.assertIn("下一A股交易日：2026-09-04", result)
            self.assertIn("不生成订单", result)

    def test_latest_formal_review_wins_over_narrow_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "events/reviews"
            review_dir.mkdir(parents=True)
            (review_dir / "2026-09-02.json").write_text(
                json.dumps({
                    "updated_at_beijing": "2026-09-02T23:18:34+08:00",
                    "review": {
                        "reviewed_at_beijing": "2026-09-02T22:00:00+08:00",
                        "risk_permission": "允许Trial",
                        "main_candidate": "电网设备ETF（159326）",
                        "action": "盘后不新增交易。",
                        "lifecycle": {"159326": "Trial继续成立", "515880": "Trial已关闭"},
                        "data_time": {"a_share_effective_close_beijing": "2026-09-02T15:00:00+08:00"},
                    },
                }, ensure_ascii=False), encoding="utf-8"
            )
            result = latest_formal_review_decision(root)
            self.assertEqual(result["risk_permission"], "允许Trial")
            self.assertEqual(result["main_candidate"], "电网设备ETF（159326）")
            self.assertIn("515880", result["lifecycle"])

    def test_trade_correction_writer_has_no_dashboard_destination(self):
        source = (ROOT / "scripts/apply_trade_fact_correction.py").read_text(encoding="utf-8")
        self.assertNotIn("upsert_formal_line(ROOT, DASHBOARD", source)


    def test_legacy_literal_newlines_are_recovered_as_real_markdown(self):
        legacy = "# ETF当前状态_DASHBOARD\\n\\n## 云端实时状态（自动同步）\\n\\n|标的|数量|\\n|-|-|\\n|黄金ETF（518880）|500|\\n\\n## 当前状态使用边界\\n\\n### 下一关键节点\\n- 下一A股交易日：2026-09-04"
        result = normalize_dashboard_projection(legacy, Path("."), {"updated_at": "2026-09-03T11:21:20+08:00"})
        self.assertGreater(len(result.splitlines()), 1)
        self.assertNotIn("\\n", result)
        self.assertEqual(result.splitlines()[0], "# ETF当前状态_DASHBOARD")
        self.assertIn("## 云端实时状态（自动同步）", result.splitlines())
        self.assertIn("|标的|数量|", result.splitlines())
        self.assertIn("## 当前状态使用边界", result.splitlines())

    def test_dashboard_newline_normalization_is_idempotent(self):
        legacy = "# ETF当前状态_DASHBOARD\\n\\n## 云端实时状态（自动同步）\\n\\n### 当前持仓事实"
        once = normalize_dashboard_projection(legacy, Path("."), {"updated_at": "2026-09-03T11:21:20+08:00"})
        twice = normalize_dashboard_projection(once, Path("."), {"updated_at": "2026-09-03T11:21:20+08:00"})
        self.assertEqual(once, twice)
        self.assertNotIn("\\n", twice)

if __name__ == "__main__":
    unittest.main()
