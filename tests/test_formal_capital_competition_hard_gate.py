from scripts.build_query_context import _evidence_requirement_plan


def _requirements(problem_id):
    return {
        item["evidence_class"]: item["required"]
        for item in _evidence_requirement_plan([
            {"problem_id": problem_id, "security": problem_id}
        ])
    }


def test_capital_competition_is_hard_required_for_holdings_and_cash():
    for problem_id in ("HOLDING:510300", "DEPLOYABLE_CASH", "NEXT_UNIT_CAPITAL_USE"):
        required = _requirements(problem_id)
        for evidence_class in (
            "ETF_RELATIVE_STRENGTH",
            "FULL_MARKET_DISCOVERY",
            "OBSERVATION_ETF",
            "TEMPORARY_DISCOVERY_CANDIDATE",
            "CASH",
            "RELEASABLE_CAPITAL",
            "HOLDING_ADDITIONAL_CAPITAL",
        ):
            assert required[evidence_class] is True


def test_risk_permission_does_not_invent_capital_gate():
    required = _requirements("RISK_PERMISSION")
    assert required["A_SHARE_STYLE_FEEDBACK"] is True
    assert required["ETF_RELATIVE_STRENGTH"] is False
    assert required["FULL_MARKET_DISCOVERY"] is False
