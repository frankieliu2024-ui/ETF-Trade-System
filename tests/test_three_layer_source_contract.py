from scripts.build_query_context import CANONICAL_FILES


def test_three_layer_source_contract_uses_existing_canonical_sources():
    assert CANONICAL_FILES["overseas_context"] == "data/state/overseas_context.json"
    assert CANONICAL_FILES["us_extended_hours_context"] == "data/state/us_extended_hours_context.json"
    assert CANONICAL_FILES["current"] == "data/state/CURRENT.json"
    assert CANONICAL_FILES["account_fact"] == "data/state/account_fact.json"
    assert CANONICAL_FILES["etf_monitor_universe"] == "config/market/etf_monitor_universe.json"


def test_three_layer_source_contract_is_not_index_only():
    required = {
        "global_risk", "rates", "fx", "overseas_industry_theme",
        "breadth", "style", "industry_theme", "liquidity_turnover",
        "capital_flow", "anomaly", "holdings", "cash", "discovery",
        "account_stock",
    }
    assert len(required) >= 10


def test_global_cross_market_review_is_mandatory_in_formal_plan():
    from scripts.build_query_context import _evidence_requirement_plan
    graph = [{"problem_id": "RISK_PERMISSION", "security": "组合"}]
    plan = _evidence_requirement_plan(graph)
    review = next(x for x in plan if x["evidence_class"] == "GLOBAL_CROSS_MARKET_REVIEW")
    assert review["required"] is True


def test_global_market_change_can_trigger_without_a_share_anomaly():
    import json
    from pathlib import Path
    config = json.loads(Path("config/market/market_monitor_config.json").read_text(encoding="utf-8"))
    rule = config["vnext_contracts"]["monitoring_layers"]["layer_1_external_drivers_cross_market_state"]["expansion_trigger"]
    assert "independently" in rule
    assert "A-share anomaly first" in rule
