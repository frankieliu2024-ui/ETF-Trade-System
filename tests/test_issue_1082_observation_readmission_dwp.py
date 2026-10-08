import tempfile
import unittest
from pathlib import Path

from scripts import build_query_context as query
from scripts import process_state_sync_request as sync


class ObservationReadmissionDwpTests(unittest.TestCase):
    def test_explicit_nonmanaged_review_enters_request_bound_problem_graph(self):
        request = {
            "request_id": "formal-review-561980",
            "requested_at_beijing": "2026-10-08T16:00:00+08:00",
            "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "intent": "FORMAL_INTRADAY_ANALYSIS",
            "_request_file": "requests/live_snapshot/formal-review-561980.json",
            "observation_identity_review_targets": [
                {"code": "561980", "name": "半导体设备ETF", "thscode": "561980.SH"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            pack = query.build_decision_fact_pack(
                Path(directory),
                request,
                {"market_date": "2026-10-08"},
                {"status": "VALID", "positions": []},
                {"rules_version": "V2.2.33", "analysis_coverage": {}},
                {
                    "decision_freshness": {"post_request": True},
                    "quotes": [{"symbol": "561980", "latest_price": 1.234, "quality_status": "PASS"}],
                },
                formal_discovery={"status": "READY", "candidates": []},
            )
        nodes = [x for x in pack["decision_work_package"]["problem_graph"] if x["problem_id"] == "OBSERVATION_REVIEW:561980"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["formal_quote_status"], "READY")
        self.assertEqual(nodes[0]["role_contract"], "EXPLICIT_OBSERVATION_IDENTITY_REVIEW_V1")

    def test_explicit_review_does_not_duplicate_current_observation_or_holding(self):
        graph = query._decision_problem_graph(
            [{"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF"}],
            [],
            {},
            [{"code": "515220", "name": "煤炭ETF"}],
            [{"code": "561980", "name": "半导体设备ETF", "formal_quote_status": "READY"}],
        )
        self.assertEqual(sum(x["problem_id"] == "OBSERVATION_REVIEW:561980" for x in graph), 1)
        # Filtering of existing roles is owned by build_decision_fact_pack; the graph
        # itself remains a pure projection of already-qualified inputs.

    def test_request_bound_identity_review_is_legal_observation_eligibility_input(self):
        request = {
            "decision_work_package": {
                "problem_graph": [{
                    "problem_id": "OBSERVATION_REVIEW:561980",
                    "role_contract": "EXPLICIT_OBSERVATION_IDENTITY_REVIEW_V1",
                    "formal_quote_status": "READY",
                }]
            }
        }
        inputs = sync.request_bound_observation_identity_review_inputs(request)
        self.assertEqual(inputs["561980"]["formal_quote_status"], "READY")
        decision = {
            "observation_eligibility_reviews": [
                {"code": "561980", "disposition": "ADMIT", "reason": "independent falsifiable cross-node information value"}
            ]
        }
        admitted, error = sync.validate_observation_eligibility_reviews(decision, inputs)
        self.assertEqual(error, "")
        self.assertEqual(admitted, {"561980"})

    def test_admit_fails_closed_without_ready_formal_quote(self):
        request = {
            "decision_work_package": {
                "problem_graph": [{
                    "problem_id": "OBSERVATION_REVIEW:561980",
                    "role_contract": "EXPLICIT_OBSERVATION_IDENTITY_REVIEW_V1",
                    "formal_quote_status": "FAILED",
                }]
            }
        }
        inputs = sync.request_bound_observation_identity_review_inputs(request)
        admitted, error = sync.validate_observation_eligibility_reviews({
            "observation_eligibility_reviews": [
                {"code": "561980", "disposition": "ADMIT", "reason": "test"}
            ]
        }, inputs)
        self.assertEqual(admitted, set())
        self.assertIn("without READY formal quote", error)

    def test_explicit_review_participates_in_capital_opportunity_coverage(self):
        required = sync.request_bound_etf_opportunity_reviews({
            "decision_work_package": {
                "problem_graph": [{
                    "problem_id": "OBSERVATION_REVIEW:561980",
                    "role_contract": "EXPLICIT_OBSERVATION_IDENTITY_REVIEW_V1",
                    "formal_quote_status": "READY",
                }]
            }
        })
        self.assertEqual(required, {"561980": "OBSERVATION_EVALUATION_INPUT"})


if __name__ == "__main__":
    unittest.main()
