import inspect
import unittest

from scripts import build_query_context
from scripts.build_query_context import _decision_problem_graph, _evidence_requirement_plan


class BaseStockResearchBoundaryTest(unittest.TestCase):
    def test_historical_base_stock_screen_is_not_a_formal_decision_object_source(self):
        source = inspect.getsource(build_query_context)
        self.assertNotIn("research/base_stock_screen", source)
        self.assertNotIn("_latest_base_stock_replacement_candidates", source)
        self.assertNotIn("base_stock_replacement_candidates", source)

    def test_actual_account_stock_remains_in_full_capital_competition(self):
        positions = [{
            "code": "300750",
            "name": "宁德时代",
            "asset_type": "STOCK",
            "quantity": 200,
            "market_value": 57350,
        }]
        graph = _decision_problem_graph(positions, [], {}, [], [])
        holding = next(x for x in graph if x["problem_id"] == "HOLDING:300750")
        self.assertTrue(holding["capital_efficiency_release_required"])
        self.assertIn("HOLD/REDUCE/EXIT", holding["required_business_judgment"])
        plan = _evidence_requirement_plan(graph)
        classes = {
            x["evidence_class"]
            for x in plan
            if x["target_problem_id"] == "HOLDING:300750"
        }
        self.assertTrue({
            "ACCOUNT_STOCK",
            "CASH",
            "RELEASABLE_CAPITAL",
            "TEMPORARY_DISCOVERY_CANDIDATE",
        }.issubset(classes))

    def test_current_etf_discovery_still_enters_problem_graph(self):
        discovery = [{
            "code": "159029",
            "name": "ETF candidate",
            "formal_quote_status": "READY",
        }]
        graph = _decision_problem_graph([], discovery, {}, [], [])
        ids = {x["problem_id"] for x in graph}
        self.assertIn("DISCOVERY:159029", ids)
        self.assertFalse(any(x.startswith("BASE_STOCK_REPLACEMENT:") for x in ids))


if __name__ == "__main__":
    unittest.main()
