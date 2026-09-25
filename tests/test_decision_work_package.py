import unittest

from scripts.build_query_context import (
    _decision_problem_graph,
    _evidence_requirement_plan,
    _decision_work_package,
)


class DecisionWorkPackageTests(unittest.TestCase):
    def test_energy_candidate_gets_candidate_relevant_external_requirements(self):
        graph = _decision_problem_graph(
            [{"code": "159981", "name": "能源化工ETF", "asset_type": "ETF", "market_value": 10000}],
            [],
            {"deployable_cash": 13169.54},
        )
        plan = _evidence_requirement_plan(graph)
        energy = {x["evidence_class"] for x in plan if x["target_problem_id"] == "HOLDING:159981"}
        self.assertTrue({"COMMODITY", "FX", "RATES", "OVERSEAS_INDUSTRY_CHAIN", "HK_INDUSTRY_CHAIN"} <= energy)

    def test_semiconductor_candidate_does_not_reuse_energy_commodity_list(self):
        graph = _decision_problem_graph(
            [{"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF", "market_value": 10000}],
            [],
            {"deployable_cash": 13169.54},
        )
        plan = _evidence_requirement_plan(graph)
        tech = {x["evidence_class"] for x in plan if x["target_problem_id"] == "HOLDING:561980"}
        self.assertTrue({"FX", "RATES", "OVERSEAS_INDUSTRY_CHAIN", "HK_INDUSTRY_CHAIN"} <= tech)
        self.assertNotIn("COMMODITY", tech)

    def test_every_position_has_hold_reduce_exit_and_add_capital_problems(self):
        graph = _decision_problem_graph(
            [
                {"code": "300750", "name": "宁德时代", "asset_type": "STOCK"},
                {"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF"},
            ],
            [{"code": "159127", "name": "观察候选"}],
            {"deployable_cash": 1000},
        )
        ids = {x["problem_id"] for x in graph}
        self.assertIn("HOLDING:300750", ids)
        self.assertIn("HELD_ETF_ADD:561980", ids)
        self.assertIn("DISCOVERY:159127", ids)
        holding = next(x for x in graph if x["problem_id"] == "HOLDING:561980")
        self.assertEqual(set(holding["alternatives"]), {"HOLD", "REDUCE", "EXIT"})

    def test_work_package_exposes_missing_required_evidence_and_no_schema_knowledge(self):
        graph = _decision_problem_graph(
            [{"code": "159981", "name": "能源化工ETF", "asset_type": "ETF"}],
            [],
            {},
        )
        plan = _evidence_requirement_plan(graph)
        package = _decision_work_package(graph, plan, [])
        self.assertFalse(package["decision_marginal_stop"]["expansion_complete"])
        self.assertTrue(package["decision_marginal_stop"]["unresolved_required_requirements"])
        self.assertFalse(package["response_contract"]["schema_knowledge_required"])


if __name__ == "__main__":
    unittest.main()
