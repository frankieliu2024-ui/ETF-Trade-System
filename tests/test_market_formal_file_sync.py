import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_formal_files import build_archive_fact_block, canonical_risk, normalize_dashboard_projection, replace_block, update_experience


class FormalFileSyncTests(unittest.TestCase):
    def test_replace_block_is_idempotent(self):
        text = "# Dashboard\n\n<!-- START -->\nold\n<!-- END -->\nbody\n"
        once = replace_block(text, "<!-- START -->", "<!-- END -->", "new")
        twice = replace_block(once, "<!-- START -->", "<!-- END -->", "new")
        self.assertEqual(once, twice)
        self.assertEqual(twice.count("<!-- START -->"), 1)

    def test_confirmed_fee_updates_matching_trade_row_only(self):
        text = (
            "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|备注|\n"
            "|-|-|-|-|-|-|-|-|-|-|\n"
            "|2026-08-25 11:22:34|半导体设备ETF（561980）|561980|卖出|13,000|0.663|8,619.00|待确认|8,619.00|费用未显示|\n"
        )
        account = {"trades": [{
            "trade_time": "2026-08-25T11:22:00+08:00",
            "code": "561980",
            "action": "SELL",
            "quantity": 13000,
            "price": 0.663,
            "fee": 5.0,
            "fee_status": "CONFIRMED",
            "amount": 8614.0,
        }]}
        updated, count = update_experience(text, account)
        self.assertEqual(count, 1)
        self.assertIn("|5.00|8,614.00|", updated)
        self.assertIn("费用已确认", updated)

    def test_no_confirmed_fee_does_not_invent_one(self):
        account = {"trades": [{"code": "513180", "fee_status": "PENDING"}]}
        block = build_archive_fact_block(account)
        self.assertIn("未新增已确认费用", block)
        self.assertNotIn("5.00元", block)

    def test_legacy_current_structure_is_neutralized(self):
        text = "|ETF层当前结构|持仓ETF：旧持仓；观察ETF：电网设备ETF（159326）|\\n"
        normalized = normalize_dashboard_projection(text)
        self.assertIn("canonical account projection", normalized)
        self.assertNotIn("观察ETF：电网设备ETF（159326）", normalized)

    def test_risk_reads_maintained_known_net_only(self):
        self.assertEqual(canonical_risk({"summary": {"known_net_current_strategy_return_pct": -8.3}}), -8.3)


if __name__ == "__main__":
    unittest.main()
