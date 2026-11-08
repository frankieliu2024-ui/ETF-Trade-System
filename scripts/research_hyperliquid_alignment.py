#!/usr/bin/env python3
"""Build a research-only Hyperliquid-to-A-share alignment view.

The canonical exposure map remains the only object-mapping source. This module
adds research semantics only: transmission level, result variables and failure
conditions. It never writes production state or trading decisions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "config" / "market" / "hyperliquid_etf_exposure_map.json"

FAMILY_RULES = {
    "CHINA_MEMORY_CHAIN": {
        "transmission_levels": ["DIRECT_REFERENCE", "INDUSTRY", "SECTOR_BREADTH"],
        "result_variables": ["direct_open_gap", "memory_industry_open_gap", "industry_breadth", "post_open_reversal"],
        "failure_conditions": ["wrong_or_unverified_A_share_code", "PIT_mismatch", "industry_breadth_missing"],
    },
    "MEMORY": {
        "transmission_levels": ["INDUSTRY", "SECTOR_BREADTH"],
        "result_variables": ["memory_industry_open_gap", "industry_breadth", "post_open_reversal"],
        "failure_conditions": ["no_stable_memory_industry_universe", "PIT_mismatch", "single_leader_only"],
    },
    "SEMICONDUCTOR": {
        "transmission_levels": ["INDUSTRY", "SECTOR_BREADTH"],
        "result_variables": ["semiconductor_industry_open_gap", "industry_breadth", "post_open_reversal"],
        "failure_conditions": ["industry_definition_drift", "PIT_mismatch", "single_ETF_only"],
    },
    "SEMICONDUCTOR_HIGH_BETA": {
        "transmission_levels": ["INDUSTRY", "RISK_APPETITE", "VOLATILITY"],
        "result_variables": ["semiconductor_industry_open_gap", "risk_appetite_open_gap", "opening_volatility", "post_open_reversal"],
        "failure_conditions": ["leverage_not_adjusted", "crowding_data_missing", "PIT_mismatch"],
    },
    "AI_COMPUTE": {
        "transmission_levels": ["INDUSTRY", "SUPPLY_CHAIN", "RISK_APPETITE"],
        "result_variables": ["compute_chain_open_gap", "industry_breadth", "risk_appetite_open_gap", "post_open_reversal"],
        "failure_conditions": ["supply_chain_universe_drift", "single_leader_only", "PIT_mismatch"],
    },
    "AI_NETWORKING": {
        "transmission_levels": ["SUPPLY_CHAIN", "INDUSTRY", "SECTOR_BREADTH"],
        "result_variables": ["networking_chain_open_gap", "industry_breadth", "post_open_reversal"],
        "failure_conditions": ["supply_chain_universe_drift", "single_leader_only", "PIT_mismatch"],
    },
    "BIOTECH": {
        "transmission_levels": ["INDUSTRY", "SECTOR_BREADTH"],
        "result_variables": ["biotech_industry_open_gap", "industry_breadth", "post_open_reversal"],
        "failure_conditions": ["industry_definition_drift", "single_leader_only", "PIT_mismatch"],
    },
    "ENERGY": {
        "transmission_levels": ["COMMODITY", "INDUSTRY", "SECTOR_BREADTH"],
        "result_variables": ["energy_industry_open_gap", "commodity_open_gap", "industry_breadth", "post_open_reversal"],
        "failure_conditions": ["commodity_currency_effect_unadjusted", "industry_definition_drift", "PIT_mismatch"],
    },
    "GOLD": {
        "transmission_levels": ["COMMODITY", "INDUSTRY", "RISK_APPETITE"],
        "result_variables": ["gold_industry_open_gap", "commodity_open_gap", "risk_appetite_open_gap", "post_open_reversal"],
        "failure_conditions": ["USD_CNH_effect_unadjusted", "gold_equity_proxy_drift", "PIT_mismatch"],
    },
    "INDUSTRIAL_METALS": {
        "transmission_levels": ["COMMODITY", "INDUSTRY", "SECTOR_BREADTH"],
        "result_variables": ["metals_industry_open_gap", "commodity_open_gap", "industry_breadth", "post_open_reversal"],
        "failure_conditions": ["commodity_currency_effect_unadjusted", "industry_definition_drift", "PIT_mismatch"],
    },
    "GLOBAL_RISK_APPETITE": {
        "transmission_levels": ["RISK_APPETITE", "STYLE"],
        "result_variables": ["broad_market_open_gap", "growth_value_relative_open_gap", "opening_volatility", "post_open_reversal"],
        "failure_conditions": ["no_A_share_style_target", "PIT_mismatch", "single_index_overinterpretation"],
    },
    "KOREA_EQUITY": {
        "transmission_levels": ["REGIONAL_RISK_APPETITE", "SUPPLY_CHAIN"],
        "result_variables": ["regional_risk_open_gap", "technology_chain_open_gap", "post_open_reversal"],
        "failure_conditions": ["regional_to_A_share_channel_unverified", "PIT_mismatch"],
    },
    "JAPAN_EQUITY": {
        "transmission_levels": ["REGIONAL_RISK_APPETITE", "STYLE"],
        "result_variables": ["regional_risk_open_gap", "style_open_gap", "post_open_reversal"],
        "failure_conditions": ["regional_to_A_share_channel_unverified", "PIT_mismatch"],
    },
    "TAIWAN_EQUITY": {
        "transmission_levels": ["REGIONAL_RISK_APPETITE", "SUPPLY_CHAIN"],
        "result_variables": ["regional_risk_open_gap", "technology_chain_open_gap", "post_open_reversal"],
        "failure_conditions": ["regional_to_A_share_channel_unverified", "PIT_mismatch"],
    },
    "CHINA_TECH_RISK": {
        "transmission_levels": ["REGIONAL_RISK_APPETITE", "INDUSTRY"],
        "result_variables": ["technology_risk_open_gap", "internet_consumer_open_gap", "post_open_reversal"],
        "failure_conditions": ["A_share_channel_unverified", "PIT_mismatch"],
    },
}

def build() -> dict:
    config = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    rows = []
    for mapping in config.get("asset_mappings", []):
        family = str(mapping.get("family") or "")
        rule = FAMILY_RULES.get(family)
        rows.append({
            "asset": mapping.get("asset"),
            "family": family,
            "etf_codes": mapping.get("etf_codes", []),
            "name_markers": mapping.get("name_markers", []),
            "transmission_levels": rule["transmission_levels"] if rule else ["UNCLASSIFIED"],
            "result_variables": rule["result_variables"] if rule else ["mapping_review_required"],
            "failure_conditions": rule["failure_conditions"] if rule else ["family_rule_missing"],
            "research_status": "READY_FOR_ALIGNMENT" if rule else "MAPPING_REVIEW_REQUIRED",
        })
    return {
        "schema_version": "research-hl-alignment-v1",
        "mode": "RESEARCH_ONLY_EXPLORATORY",
        "read_only": True,
        "canonical_mapping_source": "config/market/hyperliquid_etf_exposure_map.json",
        "family_rule_count": len(FAMILY_RULES),
        "alignments": rows,
        "boundary": "Research semantics do not change canonical mapping, production state, formal decision, trading permission, amount or order.",
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    result = json.dumps(build(), ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(result + "\n", encoding="utf-8")
    print(result)
