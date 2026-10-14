from scripts import build_e2e_status as e2e

def test_non_formal_request_is_not_treated_as_formal(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e, "ROOT", tmp_path)
    path = tmp_path / "requests" / "live_snapshot" / "system-review.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"request_id":"system-review","request_type":"SYSTEM_REVIEW","source":"CHATGPT_SYSTEM_REVIEW"}',
        encoding="utf-8",
    )
    assert e2e.formal_parent_request("system-review") == {}
    query = {
        "decision_fact_pack": {
            "trigger": {
                "request_id": "system-review",
                "requested_at_beijing": "2026-10-14T09:00:00+08:00",
            },
            "formal_reply_freeze": {
                "request_id": "system-review",
                "status": "READY",
                "reply_freezable": True,
            },
            "decision_work_package": {
                "problem_graph": [{"problem_id": "RISK_PERMISSION"}],
                "response_contract": {},
            },
        }
    }
    result = e2e.context_component(query, {})
    assert result["request_bound"] is False
    assert result["formal_reply_gate"] is None

def test_three_formal_sources_are_recognized_as_parent_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e, "ROOT", tmp_path)
    directory = tmp_path / "requests" / "live_snapshot"
    directory.mkdir(parents=True)
    sources = {
        "manual": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
        "manual-task": "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
        "automatic": "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
    }
    for request_id, source in sources.items():
        (directory / f"{request_id}.json").write_text(
            '{"request_id":"%s","request_type":"MARKET_QUOTE_REFRESH","source":"%s",'
            '"intent":{"query_intent":"FORMAL_INTRADAY_ANALYSIS"}}'
            % (request_id, source),
            encoding="utf-8",
        )
        assert e2e.formal_parent_request(request_id)["source"] == source
