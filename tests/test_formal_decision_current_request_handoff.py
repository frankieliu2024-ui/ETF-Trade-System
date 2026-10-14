from scripts.formal_request_consumer import classify_formal_request_consumer_state

def _context(request_id="req-1"):
    return {
        "decision_fact_pack": {
            "trigger": {"request_id": request_id},
            "formal_reply_freeze": {
                "request_id": request_id,
                "status": "READY",
                "reply_freezable": True,
            },
            "decision_work_package": {
                "problem_graph": [{"problem_id": "RISK_PERMISSION"}],
                "response_contract": {},
            },
        }
    }

def test_ready_handoff_is_explicitly_current_request_bound():
    result = classify_formal_request_consumer_state("req-1", _context())
    assert result["consumer_status"] if "consumer_status" in result else result["status"] == "BUSINESS_DECISION_READY"
    handoff = result["ready_handoff"]
    assert handoff["parent_request_id"] == "req-1"
    assert handoff["binding_rules"]["parent_request_id"] == "req-1"
    assert handoff["historical_fallback_forbidden"] is True
    assert "content_hash" in handoff["required_payload_fields"]
    assert handoff["target_path"].endswith("req-1__business_decision_source.json")

def test_old_context_cannot_authorize_current_request():
    result = classify_formal_request_consumer_state("req-current", _context("req-old"))
    assert result["actor_action"] == "CONTINUE_SAME_REQUEST_SILENTLY"
    assert result["user_output_allowed"] is False
    assert result["reply_eligible"] is False
