import json
from pathlib import Path


def test_formal_runtime_completion_does_not_require_fact_request_binding():
    from scripts.scheduled_review_completion import build_formal_runtime_completion_request

    body = """【ETF正式决策】2026-10-26 21:00 超时判断

【判断时点与事实边界】
测试正文。
"""
    payload = build_formal_runtime_completion_request(
        run_identity={
            "task_run_id": "manual-formal-20261026T210000-test",
            "occurrence_id": "manual_formal_20261026T210000_test",
            "requested_at_beijing": "2026-10-26T21:00:00+08:00",
            "entry_mode": "MANUAL_SCHEDULED_TASK",
        },
        market_date="2026-10-26",
        final_content=body,
        decision_path="timeout",
    )
    assert payload["request_type"] == "SCHEDULED_REVIEW_COMPLETION"
    assert payload["fact_source"] == "CURRENT_MAIN_LATEST_VALID_FIXED_PERIOD_FACTS"
    assert payload["decision_path"] == "timeout"
    assert "parent_request_id" not in payload
    assert "fact_binding" not in payload["formal_review"] if "formal_review" in payload else True
    assert payload["presentation_binding"]["report_type"] == "ETF_FORMAL_DECISION"


def test_direct_writer_is_the_formal_completion_ingress():
    text = Path("scripts/scheduled_review_completion.py").read_text(encoding="utf-8")
    assert "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER" in text
    assert "FORMAL_CURRENT_MAIN_DECISION" in text
