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


def test_formal_completion_rejects_holdings_without_zero_base_counterfactual():
    import pytest
    from scripts.scheduled_review_completion import build_formal_runtime_completion_request

    headings = (
        "【判断时点与事实边界】", "【当前正式判断】", "【风险与市场状态】",
        "【三层监测】", "【全市场发现逐只评估】", "【观察ETF逐只评估】",
        "【机会ETF逐只评估】", "【全市场机会与资本竞争】", "【现金管理】",
        "【全部实际持仓】", "【最终管理动作】", "【关键风险与改变判断的条件】",
        "【信息不足在哪里】", "【执行边界】", "【置信度】", "【最可能出错的地方】",
    )
    sections = {
        "【风险与市场状态】": "风险许可允许继续评估，但不能替代资本比较。",
        "【机会ETF逐只评估】": "1）当前候选：Trial按5,000元标准评估，直接Confirm与Trial及0元比较；当前未达到资格，决定性缺口仍在，下一复核节点为下次正式决策。",
        "【全市场机会与资本竞争】": "现金保留、持仓继续持有、追加、部分减持释放、全部退出和机会均相较。因为资本效率和机会成本，当前胜出用途为保留现金，下一单位资本当前最有效用途为保留现金。",
        "【全部实际持仓】": "1）测试ETF（159999）：持有管理，不追加。相较现金和机会，继续占资优于释放；继续持有、追加、部分减持、全部退出均已比较。若趋势变化则重新评估。",
    }
    body = "【ETF正式决策】2026-10-30 09:00 正式判断\n\n" + "\n\n".join(
        heading + "\n\n" + sections.get(heading, "") for heading in headings
    )
    with pytest.raises(ValueError, match="zero_base_reentry_counterfactual"):
        build_formal_runtime_completion_request(
            run_identity={
                "task_run_id": "manual-formal-20261030T090000-test",
                "occurrence_id": "manual_formal_20261030T090000_test",
                "requested_at_beijing": "2026-10-30T09:00:00+08:00",
                "entry_mode": "MANUAL_SCHEDULED_TASK",
            },
            market_date="2026-10-30",
            final_content=body,
            decision_path="formal",
        )
