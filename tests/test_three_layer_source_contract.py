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
