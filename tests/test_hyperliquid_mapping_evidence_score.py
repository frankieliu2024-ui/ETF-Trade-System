from scripts.audit_hyperliquid_mapping_evidence import score_edges


def test_shadow_edges_require_independent_evidence_before_upgrade():
    result = score_edges(
        shadow={
            "source_context_id": "hl-test",
            "generated_at_beijing": "2026-11-09T20:50:00+08:00",
            "edges": [{
                "object_id": "xyz:SMH",
                "mapping_type": "NAME_MARKER_INFERRED",
                "source": "CONFIG_FAMILY_MARKER",
                "status": "SHADOW",
            }]
        },
        latest={
            "context_id": "hl-test",
            "observations": [{
                "object_id": "xyz:SMH",
                "pulse_comparison_status": "COMPARABLE",
            }]
        },
    )
    edge = result["edges"][0]
    assert edge["evidence_score"] > 0
    assert edge["upgrade_eligible"] is False
    assert "缺少历史同步率与领先性验证" in edge["evidence_gaps"]
    assert "缺少独立来源交叉确认" in edge["evidence_gaps"]


def test_direct_seed_is_recorded_but_not_presented_as_validated_evidence():
    result = score_edges(
        shadow={"edges": [{
            "object_id": "xyz:XYZ100",
            "mapping_type": "DIRECT_CODE",
            "source": "CONFIG_SEED",
            "status": "ACTIVE_SEED",
        }]},
        latest={"observations": []},
    )
    edge = result["edges"][0]
    assert edge["evidence_components"]["configuration_anchor"] == 1.0
    assert edge["upgrade_status"] == "ACTIVE_CANDIDATE"
    assert result["production_eligible"] is False
