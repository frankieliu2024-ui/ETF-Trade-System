from scripts.build_hyperliquid_etf_exposure_graph import build_graph


def test_graph_uses_etfs_as_terminal_nodes_and_does_not_infer_missing_history():
    result = build_graph(
        latest={"observations": [{"object_id": "xyz:CXMT"}]},
        universe={"objects": [{"code": "588000", "name": "科创50ETF"}]},
        aligned_series={},
    )
    assert result["production_eligible"] is False
    assert result["etf_node_count"] == 1
    assert result["edges"][0]["etf_code"] == "588000"
    assert result["edges"][0]["edge_status"] == "INSUFFICIENT_HISTORY"


def test_graph_creates_shadow_edge_only_from_aligned_series():
    hl = [float(i) for i in range(30)]
    etf = [float(i) * 0.8 + 1 for i in range(30)]
    result = build_graph(
        latest={"observations": [{"object_id": "xyz:SMH"}]},
        universe={"objects": [{"code": "588000", "name": "科创50ETF"}]},
        aligned_series={"xyz:SMH|588000": {"hyperliquid": hl, "etf": etf}},
    )
    edge = result["edges"][0]
    assert edge["edge_status"] == "SHADOW"
    assert edge["mapping_type"] == "DYNAMIC_STATISTICAL_EXPOSURE"
    assert edge["upgrade_eligible"] is False
    assert edge["evidence"]["out_of_sample_status"] == "NOT_RUN"
