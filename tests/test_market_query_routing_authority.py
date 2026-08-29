from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class QueryRoutingAuthorityBoundaryTest(unittest.TestCase):
    def test_query_note_is_engineering_only_and_delegates_normative_semantics(self):
        note = (ROOT / "docs/市场行情查询路由与全球时点规则_V1.0.md").read_text(encoding="utf-8")
        index = (ROOT / "ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")
        data_spec = (ROOT / "ETF与市场监测数据接口使用规范.md").read_text(encoding="utf-8")

        self.assertIn("数据与市场监测域的唯一规范性规则来源", data_spec)
        self.assertIn("查询路由技术说明", index)
        self.assertIn("不是第五个规范域", note)
        self.assertIn("ETF与市场监测数据接口使用规范.md", note)
        for entry in ("scripts/market_quote_router.py", "scripts/query_market_object.py", "scripts/query_time_market_refresh.py"):
            self.assertIn(entry, note)

        # The engineering note must not carry a second copy of market-session tables.
        for duplicated_schedule in ("美东04:00-09:30", "09:15-09:25 集合竞价", "09:30-11:30 连续交易"):
            self.assertNotIn(duplicated_schedule, note)


if __name__ == "__main__":
    unittest.main()
