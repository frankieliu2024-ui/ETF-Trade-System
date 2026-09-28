from scripts.build_query_context import _evidence_requirement_plan


def test_triggered_external_domains_are_required_for_capital_competition():
    problems = [
        {"problem_id": "NEXT_UNIT_CAPITAL_USE", "security": "下一单位资本用途"},
        {"problem_id": "HOLDING:159981", "security": "能源化工ETF"},
    ]
    plan = _evidence_requirement_plan(problems)
    by_class = {(item["target_problem_id"], item["evidence_class"]): item for item in plan}
    assert by_class[("NEXT_UNIT_CAPITAL_USE", "GLOBAL_RISK")]["required"] is True
    assert by_class[("HOLDING:159981", "COMMODITY")]["required"] is True
    assert by_class[("HOLDING:159981", "FX")]["required"] is True
    assert by_class[("HOLDING:159981", "RATES")]["required"] is True


def test_untriggered_optional_domain_is_not_added_to_plan():
    problems = [{"problem_id": "RISK_PERMISSION", "security": "风险许可"}]
    plan = _evidence_requirement_plan(problems)
    assert not any(item["evidence_class"] == "COMMODITY" for item in plan)
