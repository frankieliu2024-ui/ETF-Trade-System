from scripts.build_query_context import _evidence_requirement_plan


def test_complete_three_layer_baseline_is_required():
    plan = _evidence_requirement_plan([{"problem_id": "RISK_PERMISSION", "security": "风险许可"}])
    classes = {item["evidence_class"] for item in plan}
    expected = {"GLOBAL_RISK", "RATES", "FX", "OVERSEAS_INDUSTRY_CHAIN", "MACRO_POLICY_EVENTS", "CROSS_MARKET_ASSETS_SUPPLY_CHAIN", "A_SHARE_INDEX", "A_SHARE_BREADTH", "A_SHARE_STYLE_FEEDBACK", "A_SHARE_INDUSTRY_THEME", "A_SHARE_LIQUIDITY_TURNOVER", "A_SHARE_CAPITAL_FLOW", "A_SHARE_ANOMALY", "EXTERNAL_CONFIRMATION_STATE", "ETF_RELATIVE_STRENGTH", "FULL_MARKET_DISCOVERY", "HOLDING_ETF", "OBSERVATION_ETF", "TEMPORARY_DISCOVERY_CANDIDATE", "ACCOUNT_STOCK", "CONDITIONAL_INDUSTRY_CHAIN", "CASH", "RELEASABLE_CAPITAL", "HOLDING_ADDITIONAL_CAPITAL"}
    assert expected.issubset(classes)
    assert all(item["required"] for item in plan)


def test_commodity_remains_exposure_triggered():
    plan = _evidence_requirement_plan([{"problem_id": "HOLDING:159981", "security": "能源化工ETF"}])
    commodity = next(item for item in plan if item["evidence_class"] == "COMMODITY")
    assert commodity["required"] is True
