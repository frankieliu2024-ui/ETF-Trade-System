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
    assert {
        "GLOBAL_RISK", "COMMODITY", "RATES", "FX", "OVERSEAS_INDUSTRY_CHAIN",
        "A_SHARE_INDEX", "A_SHARE_BREADTH", "A_SHARE_STYLE_FEEDBACK",
        "A_SHARE_INDUSTRY_THEME", "A_SHARE_LIQUIDITY_TURNOVER",
        "A_SHARE_CAPITAL_FLOW", "A_SHARE_ANOMALY", "EXTERNAL_CONFIRMATION_STATE",
        "ETF_RELATIVE_STRENGTH", "FULL_MARKET_DISCOVERY", "HOLDING_ETF",
        "OBSERVATION_ETF", "TEMPORARY_DISCOVERY_CANDIDATE", "ACCOUNT_STOCK",
        "CONDITIONAL_INDUSTRY_CHAIN", "CASH", "RELEASABLE_CAPITAL",
        "HOLDING_ADDITIONAL_CAPITAL",
    }.issubset(classes)
    assert all(item["required"] for item in plan)
