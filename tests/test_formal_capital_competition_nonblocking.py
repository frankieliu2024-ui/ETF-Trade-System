from scripts.build_query_context import _decision_work_package


def test_capital_competition_evidence_is_advisory_before_final_content():
    package = _decision_work_package(
        [{"problem_id": "NEXT_UNIT_CAPITAL_USE", "security": "现金"}],
        [{"requirement_id": "NEXT_UNIT_CAPITAL_USE:ETF_RELATIVE_STRENGTH", "target_problem_id": "NEXT_UNIT_CAPITAL_USE", "evidence_class": "ETF_RELATIVE_STRENGTH", "required": True, "optional": False}],
        [],
    )
    readiness = package["capital_competition_readiness"]
    action_gate = package["capital_competition_action_gate"]
    assert readiness["hard_gate"] is False
    assert readiness["ready"] is True
    assert readiness["scope"] == "BUSINESS_ANALYSIS_AND_POSTERIOR_BDS_VALIDATION"
    assert action_gate["status"] == "ADVISORY"
    assert action_gate["blocks"] == []
    assert action_gate["post_final_content_validation"] is True
