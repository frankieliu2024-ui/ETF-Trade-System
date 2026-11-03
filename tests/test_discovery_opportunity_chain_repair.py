import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts import state_manager
from scripts.build_query_context import _decision_problem_graph
from scripts.business_decision_source import _capital_competition_opportunity_reviews


class DiscoveryOpportunityChainRepairTests(unittest.TestCase):
    def _discovery(self, status="READY"):
        return {
            "status": "READY",
            "coverage_status": "COMPLETE",
            "candidates": [{
                "code": "588080",
                "name": "测试ETF",
                "historical_context": {"status": "READY", "as_of": "2026-11-02"},
                "formal_quote_status": status,
                "formal_quote_evidence_fitness": {
                    "observation_evaluation": status in {"READY", "SESSION_REFERENCE_READY"},
                    "capital_candidate_evaluation": status in {"READY", "SESSION_REFERENCE_READY"},
                    "executable_price": status == "READY",
                    "executable_amount": status == "READY",
                },
            }],
        }

    def test_quote_states_are_not_collapsed(self):
        base = {"capital_comparison": {}}
        self.assertEqual(
            state_manager._extend_capital_comparison_with_discovery(base, self._discovery("READY"))
            ["observation_eligibility_inputs"][0]["data_availability"], "READY")
        reference = state_manager._extend_capital_comparison_with_discovery(
            base, self._discovery("SESSION_REFERENCE_READY"))
        item = reference["observation_eligibility_inputs"][0]
        self.assertEqual(item["data_availability"], "REFERENCE_READY")
        self.assertEqual(item["formal_quote_status"], "SESSION_REFERENCE_READY")
        self.assertFalse(item["discovery_evidence"]["formal_quote_evidence_fitness"]["executable_amount"])
        self.assertEqual(
            state_manager._extend_capital_comparison_with_discovery(base, self._discovery("UNAVAILABLE"))
            ["observation_eligibility_inputs"][0]["data_availability"], "UNAVAILABLE")

    def test_discovery_opportunity_does_not_require_observation_identity(self):
        graph = _decision_problem_graph([], [{"code": "588080", "name": "测试ETF"}], {})
        ids = {str(x.get("problem_id")) for x in graph}
        self.assertIn("DISCOVERY:588080", ids)
        self.assertNotIn("OBSERVATION:588080", ids)

    def test_eight_discoveries_are_consumed_as_node_opportunity_reviews(self):
        codes = ["159133", "159268", "159616", "159798", "512070", "513220", "515030", "562910"]
        graph = [{"problem_id": f"DISCOVERY:{code}"} for code in codes]
        reviews = [
            {"code": code, "category": "OBSERVATION_EVALUATION_INPUT",
             "opportunity_status": "待正式判断", "conclusion": "eligible for formal review",
             "reason": "formal quote and historical evidence available"}
            for code in codes
        ]
        result = _capital_competition_opportunity_reviews(reviews, graph)
        self.assertEqual([item["code"] for item in result], codes)
        self.assertEqual(len(result), 8)

if __name__ == "__main__":
    unittest.main()
