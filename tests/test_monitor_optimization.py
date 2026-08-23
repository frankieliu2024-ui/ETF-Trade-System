import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_optimization_actions_and_three_layers():
    config = json.loads((ROOT / "config" / "market" / "market_monitor_config.json").read_text(encoding="utf-8"))
    layers = config["monitoring_layers"]
    assert set(layers) == {"index_monitor", "etf_monitor", "stock_monitor"}
    assert next(x for x in layers["index_monitor"] if x["id"] == "SOX")["status"] == "ACTIVE"
    assert next(x for x in layers["index_monitor"] if x["id"] == "HSTECH")["status"] == "INACTIVE"
    assert config["guardrails"]["no_scope_expansion"] is True

def test_matrix_contains_only_allowed_actions_and_no_trade_output():
    matrix = (ROOT / "config" / "market" / "market_monitor_optimization_matrix.md").read_text(encoding="utf-8")
    for action in ["KEEP", "REPLACE", "REMOVE", "UPGRADE", "DOWNGRADE"]:
        assert action in matrix
    assert "交易建议" not in matrix
    assert "买入" not in matrix
    assert "卖出" not in matrix

def test_provider_priority_keeps_hstech_proxy_fallback():
    priority = json.loads((ROOT / "config" / "market" / "provider_priority.json").read_text(encoding="utf-8"))
    assert priority["objects"]["SOX"][0] == "yahoo_chart_api"
    assert priority["objects"]["HS2083"][-1] == "ETF_PROXY_513180"
