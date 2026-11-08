from scripts.hyperliquid_contribution import build_contribution


def test_preopen_contribution():
    result = build_contribution(
        context={"generated_at_beijing": "2026-11-08T07:55:00+08:00", "observations": [{"asset": "xyz:CXMT", "family": "CHINA_MEMORY_CHAIN", "direction": "negative"}]},
        task="FORMAL_DECISION",
        node="09:00",
    )
    assert result["status"] == "READY"
    assert result["phase"] == "PREOPEN_OVERNIGHT_AGGREGATION"
    assert result["mapped_object_count"] == 1
    assert "preopen" in result["node_roles"]


def test_intraday_and_post_close_roles():
    intraday = build_contribution(
        context={"generated_at_beijing": "2026-11-08T10:00:00+08:00", "observations": [{"asset": "xyz:SMH", "family": "SEMICONDUCTOR"}]},
        task="FORMAL_DECISION",
        node="10:00",
    )
    post_close = build_contribution(
        context={"generated_at_beijing": "2026-11-08T16:05:00+08:00", "observations": [{"asset": "xyz:GOLD", "family": "GOLD"}]},
        task="TRADE_REVIEW",
        node="20:30",
    )
    assert intraday["phase"] == "A_SHARE_ACTIVE_WINDOW"
    assert post_close["phase"] == "POST_WINDOW_REFERENCE"
    assert post_close["task"] == "TRADE_REVIEW"
