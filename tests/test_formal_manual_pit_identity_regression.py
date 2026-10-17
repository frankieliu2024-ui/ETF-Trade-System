import unittest
from unittest.mock import patch

from scripts import business_decision_source as bds
from scripts.manual_formal_decision_completion import build_structured_completion_request


class FormalManualPitIdentityRegressionTests(unittest.TestCase):
    def test_parent_bound_bds_request_id_is_valid(self):
        source = {
            "request_type": "BUSINESS_DECISION_SOURCE",
            "request_id": "parent__business_decision_source",
            "parent_request_id": "parent",
            "formal_entry_source": "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
            "entry_mode": "MANUAL_SCHEDULED_TASK",
            "task_id": "ETF正式决策",
            "task_run_id": "run-1",
            "occurrence_id": "manual-scheduled-1",
            "market_date": "2026-10-17",
        }
        bds._validate_formal_entry_identity(source)
        bds._validate_request_bound_identity(source)

    def test_builder_preflight_preserves_manual_task_pit_identity(self):
        source_request = {
            "request_id": "parent",
            "source": "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
            "task_id": "ETF正式决策",
            "task_run_id": "run-1",
            "occurrence_id": "manual-scheduled-1",
            "market_date": "2026-10-17",
            "requested_at_beijing": "2026-10-17T08:21:00+08:00",
            "consumed_snapshot": "data/market/snapshots/current.json",
            "intent": "FORMAL_INTRADAY_ANALYSIS",
        }
        decision_response = {
            "answers": {
                "RISK_PERMISSION": {"final_action": "禁止新增"},
            }
        }
        work_package = {"problem_graph": [{"problem_id": "RISK_PERMISSION"}]}
        captured = {}

        def project(source, response, package):
            captured.update(source)
            return source

        with patch(
            "scripts.manual_formal_decision_completion.project_decision_response",
            side_effect=project,
        ), patch(
            "scripts.manual_formal_decision_completion.validate_source",
            return_value=None,
        ):
            payload = build_structured_completion_request(
                source_request,
                {"decision_id": "decision-1"},
                decision_response,
                work_package,
                "data/market/snapshots/current.json",
            )

        self.assertEqual(captured["request_id"], "parent__business_decision_source")
        self.assertEqual(captured["parent_request_id"], "parent")
        self.assertEqual(
            captured["formal_entry_source"],
            "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
        )
        self.assertEqual(captured["entry_mode"], "MANUAL_SCHEDULED_TASK")
        self.assertEqual(payload["formal_entry_source"], captured["formal_entry_source"])
        self.assertEqual(payload["entry_mode"], captured["entry_mode"])


if __name__ == "__main__":
    unittest.main()
