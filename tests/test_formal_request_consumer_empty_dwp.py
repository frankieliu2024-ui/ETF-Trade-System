from scripts.formal_request_consumer import classify_formal_request_consumer_state


def _context(*, request_id="req-1", populated=True):
    pack = {
        "trigger": {"request_id": request_id},
        "formal_reply_freeze": {
            "request_id": request_id,
            "status": "READY",
            "reply_freezable": True,
        },
    }
    if populated:
        pack["decision_work_package"] = {
            "problem_graph": [{"problem_id": "MAIN_CANDIDATE"}],
            "response_contract": {"required_sections": ["business_conclusion"]},
        }
    return {"decision_fact_pack": pack}


def test_empty_request_bound_pack_cannot_be_business_ready():
    state = classify_formal_request_consumer_state("req-1", _context(populated=False))
    assert state["status"] == "REQUEST_BOUND_FACTS_BUILDING"
    assert state["user_output_allowed"] is False
    assert state["continue_same_request"] is True


def test_populated_request_bound_pack_is_business_ready():
    state = classify_formal_request_consumer_state("req-1", _context())
    assert state["status"] == "BUSINESS_DECISION_READY"
    assert state["user_output_allowed"] is True
    assert state["reply_eligible"] is True


def test_previous_request_cannot_authorize_current_request():
    state = classify_formal_request_consumer_state("req-2", _context(request_id="req-1"))
    assert state["status"] == "REQUEST_BOUND_FACTS_BUILDING"
    assert state["user_output_allowed"] is False
