import json

from scripts.build_post_market_review import load_intraday_formal_decision_index


def test_close_review_keeps_latest_intraday_formal_decision(tmp_path):
    reviews = tmp_path / "events" / "reviews"
    reviews.mkdir(parents=True)
    payload = {
        "event_type": "FORMAL_DECISION_REPORT",
        "decision_id": "fd-test",
        "reviewed_at_beijing": "2026-11-05T15:06:20+08:00",
        "source_completion_path": "requests/live_snapshot/fd-test.json",
        "presentation_binding": {
            "report_type": "ETF_FORMAL_DECISION",
            "report_id": "rpt-test",
            "request_id": "fd-test",
            "full_content": "Discovery于14:59:53形成，共11个候选。\\nHyperliquid本次采集窗口约为14:58:30—14:58:32，质量通过。",
        },
    }
    (reviews / "formal_test.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = load_intraday_formal_decision_index(tmp_path, "2026-11-05")

    assert result["status"] == "READY"
    assert result["latest"]["decision_id"] == "fd-test"
    assert any("14:59:53" in line for line in result["latest"]["discovery_hyperliquid_statements"])
    assert any("14:58:30" in line for line in result["latest"]["discovery_hyperliquid_statements"])


def test_empty_intraday_index_is_explicit(tmp_path):
    result = load_intraday_formal_decision_index(tmp_path, "2026-11-05")
    assert result["status"] == "MISSING"
    assert result["latest"] is None
