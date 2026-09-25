import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def contract():
    config = json.loads((ROOT / "config/market/market_monitor_config.json").read_text(encoding="utf-8"))
    return config["vnext_contracts"]


def test_vnext_contract_has_three_business_layers_without_trade_authority():
    layers = contract()["monitoring_layers"]
    assert set(layers) == {
        "layer_1_external_drivers_cross_market_state",
        "layer_2_china_internal_market_state",
        "layer_3_etf_opportunity_and_capital_state",
    }
    assert "breadth" in layers["layer_2_china_internal_market_state"]["fact_domains"]
    assert "capital_flow" in layers["layer_2_china_internal_market_state"]["fact_domains"]
    assert layers["layer_3_etf_opportunity_and_capital_state"]["capital_competition_owner"] == "MASTER_and_ChatGPT"
    assert all("action" not in value.get("action_boundary", "") or "only" in value["action_boundary"] for value in layers.values())


def test_transmission_metadata_is_selection_only():
    metadata = contract()["transmission_metadata"]
    assert set(("transmission_tags", "external_drivers", "china_feedback", "etf_family")).issubset(metadata["fields"])
    assert "ranking" in metadata["forbidden_uses"]
    assert "trial_confirm_sell" in metadata["forbidden_uses"]


def test_unified_evidence_contract_preserves_pit_and_separate_eligibility():
    evidence = contract()["evidence_contract"]
    required = {"object", "fact_type", "source", "provider_as_of", "received_at", "market_phase", "actual_age", "quality", "PIT", "freshness", "decision_eligible", "execution_eligible", "fallback_status", "decision_use", "evidence_identity"}
    assert required.issubset(evidence["required_fields"])
    assert set(evidence["decision_use"]) == {"DECISION_CRITICAL", "DECISION_ENHANCING", "CONTEXT_ONLY", "RESEARCH"}
    assert evidence["execution_rule"] == "execution_eligible_is_independent_from_decision_eligible"


def test_discovery_identity_and_dual_entrypoints_share_one_pool():
    discovery = contract()["discovery_evidence_identity"]
    assert set(discovery["components"]) == {"market_date", "market_node", "universe_identity", "decision_relevant_market_delta", "required_history_end_date"}
    entrypoints = contract()["discovery_entrypoints"]
    assert "both_entries_share_one_candidate_eligibility_pool_and_one_MASTER_qualification_chain" == entrypoints["convergence"]
    assert "second_discovery_engine" in entrypoints["forbidden"]


def test_decision_ready_is_distinct_from_full_system_ready():
    readiness = contract()["decision_readiness"]
    assert "risk_permission" in readiness["DECISION_READY"]
    assert "next_unit_capital_use" in readiness["DECISION_READY"]
    assert "dashboard" in readiness["FULL_SYSTEM_READY"]
    assert readiness["ordering_rule"] == "FULL_SYSTEM_READY_is_not_a_prerequisite_for_DECISION_READY"
    assert "minimum_decision_state" in readiness["state_rule"]


def test_state_builder_classification_defers_projection_and_research_without_runtime_reordering():
    classification = contract()["state_builder_classification"]
    assert "account" in classification["HOT_PATH_REQUIRED"]
    assert "research_features" in classification["ASYNC"]
    assert "dashboard_candidate" in classification["AFTER_DECISION"]
    assert classification["runtime_change_boundary"] == "classification_only_in_phase2; runtime_reordering_is_phase3"


def test_contract_is_configuration_only_and_does_not_add_runtime_topology():
    config = json.loads((ROOT / "config/market/market_monitor_config.json").read_text(encoding="utf-8"))
    assert "vnext_contracts" in config
    assert not any(key in config["vnext_contracts"] for key in ("workflow", "state_file", "producer", "checker", "queue", "executor"))


def test_formal_decision_packet_projects_existing_three_layer_evidence():
    text = (ROOT / "scripts" / "build_query_context.py").read_text(encoding="utf-8")
    assert '"three_layer_monitoring_evidence": three_layer_monitoring' in text
    assert '"market_regime_context": market_regime_context' in text
    assert '"market_structure_context": market_structure_context' in text
    assert '"breadth": bool(market_regime_context.get("etf_breadth"))' in text
    assert '"style": bool(market_regime_context.get("style_context"))' in text
    assert '"formal_discovery_status": discovery.get("status") or "NOT_REQUESTED"' in text
    assert '"actual_positions": positions' in text
    assert '"observation_inputs": observation_inputs' in text
    assert "not satisfied by fixed indices alone" in text


def test_decision_work_package_satisfaction_uses_three_layer_facts_without_auto_satisfying_optional_classes():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_query_context_under_test", ROOT / "scripts" / "build_query_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    graph = [{"problem_id": "NEXT_UNIT_CAPITAL_USE", "security": "next_unit_capital_use"}]
    plan = module._evidence_requirement_plan(graph)
    monitoring = {
        "layer_2_a_share_internal": {
            "market_regime_context": {
                "etf_breadth": {"up": 3, "down": 11},
                "style_context": {"large_vs_growth": "large_cap_relative_strength"},
            },
            "market_structure_context": {"items": [{"kind": "industry"}]},
            "fact_domains": {"breadth": True, "style": True, "industry_theme": True},
        },
        "layer_3_etf_opportunity_capital": {
            "actual_positions": [{"code": "588000"}],
            "observation_inputs": [{"code": "513180"}],
            "discovery_candidates": [],
        },
    }
    package = module._decision_work_package(graph, plan, [], three_layer_monitoring=monitoring)
    states = {x["evidence_class"]: x["satisfaction"] for x in package["evidence_requirements"]}
    assert states["A_SHARE_STYLE_FEEDBACK"] == "SATISFIED"
    assert states["ETF_RELATIVE_STRENGTH"] == "SATISFIED"
    assert states["GLOBAL_RISK"] == "INSUFFICIENT"
    assert package["decision_marginal_stop"]["expansion_complete"] is True


def test_decision_work_package_fixed_index_only_does_not_satisfy_a_share_style_feedback():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_query_context_under_test_2", ROOT / "scripts" / "build_query_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    graph = [{"problem_id": "RISK_PERMISSION", "security": "risk_permission"}]
    plan = module._evidence_requirement_plan(graph)
    monitoring = {
        "layer_2_a_share_internal": {
            "market_regime_context": {"indices": [{"code": "000001"}]},
            "market_structure_context": {},
            "fact_domains": {"index": True, "breadth": False, "style": False, "industry_theme": False},
        },
        "layer_3_etf_opportunity_capital": {},
    }
    package = module._decision_work_package(graph, plan, [], three_layer_monitoring=monitoring)
    states = {x["evidence_class"]: x["satisfaction"] for x in package["evidence_requirements"]}
    assert states["A_SHARE_STYLE_FEEDBACK"] == "INSUFFICIENT"
    assert states["ETF_RELATIVE_STRENGTH"] == "INSUFFICIENT"
    assert package["decision_marginal_stop"]["expansion_required"] is True


def test_decision_work_package_partial_three_layer_evidence_is_degraded():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_query_context_under_test_3", ROOT / "scripts" / "build_query_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    graph = [{"problem_id": "RISK_PERMISSION", "security": "risk_permission"}]
    plan = module._evidence_requirement_plan(graph)
    monitoring = {
        "layer_2_a_share_internal": {
            "market_regime_context": {"etf_breadth": {"up": 3, "down": 11}},
            "market_structure_context": {},
            "fact_domains": {"breadth": True, "style": False, "industry_theme": False},
        },
        "layer_3_etf_opportunity_capital": {"actual_positions": [{"code": "588000"}]},
    }
    package = module._decision_work_package(graph, plan, [], three_layer_monitoring=monitoring)
    states = {x["evidence_class"]: x["satisfaction"] for x in package["evidence_requirements"]}
    assert states["A_SHARE_STYLE_FEEDBACK"] == "DEGRADED"
    assert states["ETF_RELATIVE_STRENGTH"] == "DEGRADED"
    assert package["decision_marginal_stop"]["expansion_required"] is True
