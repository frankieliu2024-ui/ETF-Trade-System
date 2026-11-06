from scripts.build_query_context import _cross_engine_validation


def test_external_support_is_confirmed_only_with_a_share_discovery_fact():
    binding = {"code": "513630", "external_assets": ["BABA"]}
    discovery = [{"code": "513630", "information_classes": ["PERSISTENT_STRUCTURE"]}]
    evidence = {"BABA": {"direction": "SUPPORTIVE", "decision_effect": "SUPPORT_AND_TIMING"}}
    result = _cross_engine_validation(binding, discovery, evidence)
    assert result["relation"] == "EXTERNAL_CONFIRMED_A_SHARE"
    assert result["discovery_object_present"] is True


def test_external_signal_without_a_share_confirmation_is_not_promoted():
    binding = {"code": "513630", "external_assets": ["BABA"]}
    evidence = {"BABA": {"direction": "SUPPORTIVE", "decision_effect": "SUPPORT_AND_TIMING"}}
    result = _cross_engine_validation(binding, [], evidence)
    assert result["relation"] == "EXTERNAL_NOT_TRANSMITTED"
    assert "暂不升级" in result["business_effect"]


def test_negative_external_signal_is_recorded_as_divergence():
    binding = {"code": "159567", "external_assets": ["XBI"]}
    discovery = [{"code": "159567", "information_classes": ["SHORT_TERM_CHANGE"]}]
    evidence = {"XBI": {"direction": "NEGATIVE", "decision_effect": "CONTRADICTION"}}
    result = _cross_engine_validation(binding, discovery, evidence)
    assert result["relation"] == "CROSS_MARKET_DIVERGENCE"
