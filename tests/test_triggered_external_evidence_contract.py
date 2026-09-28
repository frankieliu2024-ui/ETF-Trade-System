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


def test_fixed_external_baseline_is_present_for_risk_permission():
    problems = [{"problem_id": "RISK_PERMISSION", "security": "风险许可"}]
    plan = _evidence_requirement_plan(problems)
    classes = {item["evidence_class"] for item in plan}
    assert {"GLOBAL_RISK", "COMMODITY", "RATES", "FX", "OVERSEAS_INDUSTRY_CHAIN"}.issubset(classes)
    assert all(item["required"] for item in plan if item["evidence_class"] in {"GLOBAL_RISK", "COMMODITY", "RATES", "FX", "OVERSEAS_INDUSTRY_CHAIN"})
