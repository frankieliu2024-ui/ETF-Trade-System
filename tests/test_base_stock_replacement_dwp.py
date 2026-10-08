import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_query_context import (
    _decision_problem_graph,
    _evidence_requirement_plan,
    _latest_base_stock_replacement_candidates,
)


class BaseStockReplacementDwpTest(unittest.TestCase):
    def test_latest_screen_supplies_same_market_nonheld_candidates_without_trade_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data/state").mkdir(parents=True)
            (root / "research/base_stock_screen").mkdir(parents=True)
            (root / "data/state/asset_roles.json").write_text(json.dumps({
                "roles": {
                    "601138": {"role": "IPO_BASE_STOCK", "status": "CONFIRMED"},
                    "300750": {"role": "IPO_BASE_STOCK", "status": "CONFIRMED"},
                }
            }), encoding="utf-8")
            (root / "research/base_stock_screen/s1.json").write_text(json.dumps({
                "ok": True,
                "processed_at": "2026-10-08T20:00:00+08:00",
                "end_date": "2026-10-08",
                "records": [
                    {"code": "601138", "name": "工业富联", "market": "SH", "role": "current", "market_rank": 3, "stability_score": 10},
                    {"code": "600900", "name": "长江电力", "market": "SH", "role": "candidate", "market_rank": 1, "stability_score": 90, "ann_vol_pct": 16},
                    {"code": "000333", "name": "美的集团", "market": "SZ", "role": "candidate", "market_rank": 1, "stability_score": 85, "ann_vol_pct": 21},
                    {"code": "513180", "name": "恒生科技ETF", "market": "SH", "role": "candidate", "market_rank": 2, "stability_score": 50},
                ],
            }), encoding="utf-8")
            positions = [
                {"code": "601138", "asset_type": "STOCK"},
                {"code": "300750", "asset_type": "STOCK"},
            ]
            candidates = _latest_base_stock_replacement_candidates(
                root, positions, "2026-10-08T21:00:00+08:00"
            )
            self.assertEqual([x["code"] for x in candidates], ["600900", "000333"])
            self.assertTrue(all(x["authority"] == "RESEARCH_CANDIDATE_ONLY" for x in candidates))

    def test_replacement_is_explicit_problem_and_not_mechanical_action(self):
        replacement = [{
            "code": "600900", "name": "长江电力", "market": "SH",
            "authority": "RESEARCH_CANDIDATE_ONLY", "screen_score": 90,
        }]
        graph = _decision_problem_graph([], [], {}, [], [], replacement)
        problem = next(x for x in graph if x["problem_id"] == "BASE_STOCK_REPLACEMENT:600900")
        self.assertIn("允许结论为不替换", problem["required_business_judgment"])
        self.assertEqual(problem["research_evidence"]["authority"], "RESEARCH_CANDIDATE_ONLY")
        plan = _evidence_requirement_plan(graph)
        classes = {x["evidence_class"] for x in plan if x["target_problem_id"] == problem["problem_id"]}
        self.assertTrue({"ACCOUNT_STOCK", "CASH", "RELEASABLE_CAPITAL"}.issubset(classes))


if __name__ == "__main__":
    unittest.main()
