from scripts.hyperliquid_user_explanation import _fixed_etf_labels


def test_semiconductor_notification_mapping_excludes_nasdaq_etf():
    labels = _fixed_etf_labels({"family": "SEMICONDUCTOR"})
    assert "159941" not in " ".join(labels)
    assert any(code in " ".join(labels) for code in ("159781", "515880", "159326", "588000"))


def test_global_risk_mapping_keeps_nasdaq_etf():
    labels = _fixed_etf_labels({"family": "GLOBAL_RISK_APPETITE"})
    assert "159941" in " ".join(labels)
