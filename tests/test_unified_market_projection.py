from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.build_query_context import build_market_domain_projection


class UnifiedMarketProjectionTests(unittest.TestCase):
    def setUp(self):
        self.current = {
            "market_date": "2026-09-04",
            "market_phase": "POST_CLOSE_GRACE",
            "captured_at": "2026-09-04T15:06:35+08:00",
            "latest_snapshot": "data/market/snapshots/2026-09-04_150635.json",
            "data_freshness": {
                "status": "PASS",
                "provider": "tencent_qq",
                "provider_as_of": "2026-09-04T15:06:29+08:00",
                "captured_at_beijing": "2026-09-04T15:06:35+08:00",
            },
        }

    def test_fresh_apac_does_not_inherit_stale_a_share_date(self):
        out = build_market_domain_projection(
            self.current,
            {
                "generated_at_beijing": "2026-09-07T08:41:54+08:00",
                "quality_status": "PASS",
                "objects": {
                    "KOSPI": {
                        "provider": "naver_finance",
                        "market_phase_at_generation": "OPEN",
                        "quality_status": "PASS",
                        "freshness_status": "FRESH",
                        "latest": {
                            "market_date_local": "2026-09-07",
                            "as_of_beijing": "2026-09-07T08:41:56+08:00",
                        },
                    },
                    "N225": {
                        "provider": "eastmoney_push2",
                        "market_phase_at_generation": "OPEN",
                        "quality_status": "PASS",
                        "freshness_status": "FRESH",
                        "latest": {
                            "market_date_local": "2026-09-07",
                            "as_of_beijing": "2026-09-07T08:41:16+08:00",
                        },
                    },
                },
            },
            {"generated_at_beijing": "2026-09-07T08:42:11+08:00", "quality_status": "PASS", "objects": {}},
        )
        self.assertEqual(out["domains"]["A_SHARE"]["market_date"], "2026-09-04")
        self.assertEqual(out["domains"]["APAC"]["market_date"], "")
        objects = {x["object"]: x for x in out["domains"]["APAC"]["objects"]}
        self.assertEqual(objects["KOSPI"]["market_date"], "2026-09-07")
        self.assertEqual(objects["KOSPI"]["freshness_status"], "FRESH")
        self.assertEqual(objects["N225"]["provider_as_of_beijing"], "2026-09-07T08:41:16+08:00")

    def test_prior_reference_and_us_context_remain_object_scoped(self):
        out = build_market_domain_projection(
            self.current,
            {
                "generated_at_beijing": "2026-09-07T08:41:54+08:00",
                "quality_status": "PASS",
                "objects": {
                    "N225": {
                        "provider": "yahoo_chart_api",
                        "market_phase_at_generation": "CLOSED_NON_TRADING_DAY",
                        "quality_status": "PASS",
                        "freshness_status": "PREVIOUS_SESSION_REFERENCE",
                        "latest": {"market_date_local": "2026-09-04", "as_of_beijing": "2026-09-05T04:00:00+08:00"},
                    }
                },
            },
            {
                "generated_at_beijing": "2026-09-07T08:42:11+08:00",
                "quality_status": "PASS",
                "objects": [
                    {"object": "QQQ", "provider": "nasdaq_proxy", "market_phase_of_latest": "POST_MARKET", "quality_status": "PASS", "freshness_status": "PREVIOUS_SESSION_REFERENCE", "latest": {"market_date_local": "2026-09-05", "as_of_beijing": "2026-09-06T05:00:00+08:00"}}
                ],
            },
        )
        apac = out["domains"]["APAC"]["objects"][0]
        us = out["domains"]["US_EXTENDED_HOURS"]["objects"][0]
        self.assertEqual(apac["freshness_status"], "PREVIOUS_SESSION_REFERENCE")
        self.assertEqual(us["market_date"], "2026-09-05")
        self.assertEqual(us["market_phase"], "POST_MARKET")
        self.assertNotEqual(us["market_date"], out["domains"]["A_SHARE"]["market_date"])

    def test_workflow_rebuilds_existing_derived_owner(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/overseas-preopen-pulse.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/build_query_context.py", workflow)
        self.assertNotIn("data/state/CURRENT.json", workflow.split("Commit overseas contexts", 1)[-1])


if __name__ == "__main__":
    unittest.main()
