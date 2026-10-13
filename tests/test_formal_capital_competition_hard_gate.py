from scripts.build_query_context import _evidence_requirement_plan


def _requirements(problem_id):
    return {
        item["evidence_class"]: item["required"]
        for item in _evidence_requirement_plan([
            {"problem_id": problem_id, "security": problem_id}
        ])
    }


def test_capital_evidence_is_not_promoted_to_per_object_admission_blockers():
    required = _requirements("HOLDING:510300")
    assert required["A_SHARE_STYLE_FEEDBACK"] is True
    assert required["ETF_RELATIVE_STRENGTH"] is True
    assert required["FULL_MARKET_DISCOVERY"] is False
    assert required["CASH"] is False
    assert required["RELEASABLE_CAPITAL"] is False
    assert required["HOLDING_ADDITIONAL_CAPITAL"] is False


def test_risk_permission_does_not_invent_capital_gate():
    required = _requirements("RISK_PERMISSION")
    assert required["A_SHARE_STYLE_FEEDBACK"] is True
    assert required["ETF_RELATIVE_STRENGTH"] is True
    assert required["FULL_MARKET_DISCOVERY"] is False
