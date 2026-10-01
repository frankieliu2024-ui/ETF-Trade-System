import unittest

from scripts.business_decision_source import _validate_material_state_transitions


class FormalStateTransitionAttributionTests(unittest.TestCase):
    def package(self):
        return {
            "previous_formal_decision_baseline": {
                "decision_id": "prior",
                "risk_permission": "允许Confirm",
                "candidate_code": "159992",
                "opportunity_status": "Confirm机会",
                "holding_actions": {"159992": "HOLD"},
            },
            "evidence_requirements": [
                {"requirement_id": "RISK_PERMISSION:A_SHARE_STYLE_FEEDBACK", "target_problem_id": "RISK_PERMISSION", "satisfaction": "SATISFIED"},
                {"requirement_id": "MAIN_CANDIDATE:ETF_RELATIVE_STRENGTH", "target_problem_id": "MAIN_CANDIDATE", "satisfaction": "SATISFIED"},
                {"requirement_id": "HOLDING:159992:ETF_RELATIVE_STRENGTH", "target_problem_id": "HOLDING:159992", "satisfaction": "SATISFIED"},
            ],
        }

    def test_unchanged_material_states_need_no_artificial_delta(self):
        _validate_material_state_transitions(
            {}, self.package(), risk_permission="允许Confirm", candidate_code="159992",
            opportunity_status="Confirm机会", position_reviews=[{"security_code": "159992", "current_action": "HOLD"}],
        )

    def test_risk_permission_change_without_attribution_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "RISK_PERMISSION.state_transition_attribution"):
            _validate_material_state_transitions(
                {"RISK_PERMISSION": {}}, self.package(), risk_permission="允许Trial", candidate_code="159992",
                opportunity_status="Confirm机会", position_reviews=[{"security_code": "159992", "current_action": "HOLD"}],
            )

    def test_holiday_style_risk_change_with_unqualified_label_cannot_pass(self):
        answers = {"RISK_PERMISSION": {"state_transition_attribution": {
            "material_evidence_delta": "市场休市，没有当前执行价格",
            "evidence_requirement_ids": ["RISK_PERMISSION:MARKET_PHASE"],
        }}}
        with self.assertRaisesRegex(ValueError, "unqualified evidence"):
            _validate_material_state_transitions(
                answers, self.package(), risk_permission="允许Trial", candidate_code="159992",
                opportunity_status="Confirm机会", position_reviews=[{"security_code": "159992", "current_action": "HOLD"}],
            )

    def test_legitimate_risk_change_with_qualified_current_evidence_passes(self):
        answers = {"RISK_PERMISSION": {"state_transition_attribution": {
            "material_evidence_delta": "本次A股内部结构与风险承接出现新的实质恶化",
            "evidence_requirement_ids": ["RISK_PERMISSION:A_SHARE_STYLE_FEEDBACK"],
        }}}
        _validate_material_state_transitions(
            answers, self.package(), risk_permission="允许Trial", candidate_code="159992",
            opportunity_status="Confirm机会", position_reviews=[{"security_code": "159992", "current_action": "HOLD"}],
        )

    def test_opportunity_change_requires_its_own_attribution(self):
        with self.assertRaisesRegex(ValueError, "MAIN_CANDIDATE.state_transition_attribution"):
            _validate_material_state_transitions(
                {}, self.package(), risk_permission="允许Confirm", candidate_code="159992",
                opportunity_status="观察机会", position_reviews=[{"security_code": "159992", "current_action": "HOLD"}],
            )

    def test_holding_action_change_requires_holding_evidence_attribution(self):
        answers = {"HOLDING:159992": {"state_transition_attribution": {
            "material_evidence_delta": "目标ETF风险收益结构出现新的恶化证据",
            "evidence_requirement_ids": ["HOLDING:159992:ETF_RELATIVE_STRENGTH"],
        }}}
        _validate_material_state_transitions(
            answers, self.package(), risk_permission="允许Confirm", candidate_code="159992",
            opportunity_status="Confirm机会", position_reviews=[{"security_code": "159992", "current_action": "REDUCE"}],
        )


if __name__ == "__main__":
    unittest.main()
