from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/生产变更与并发写入协议_V1.0.md"
CFG = ROOT / "config/maintenance/production_mutation_protocol.json"


def test_governance_review_closure_is_ssot_driven_and_thin() -> None:
    doc = DOC.read_text(encoding="utf-8")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    contract = cfg["change_admission"]["review_closure_contract"]

    assert "生产变更与并发写入协议 V1.8" in doc
    assert cfg["normative_contract"]["version"] == "V1.8"

    assert contract["forbid_chat_or_task_prompt_as_only_source"] is True
    assert contract["reclassify_against_latest_main"] is True
    assert contract["historical_title_or_admission_is_not_current_truth"] is True
    assert contract["scheduled_review_role"] == "WAKEUP_AND_ANTI_OMISSION_ONLY"
    assert contract["scheduled_review_must_read_current_ssot"] is True
    assert contract["forbid_parallel_pending_rule_or_state_store"] is True

    for marker in (
        "聊天记忆和定时任务prompt不得成为唯一待办来源",
        "任何复核都必须基于latest main重新分类当前事项",
        "定时复核只是唤醒与防遗漏机制，不是第二套治理规则源",
        "不为保存待办队列新增平行规则库、状态副本或专用workflow",
    ):
        assert marker in doc


def test_governance_review_closure_does_not_create_new_admission_state() -> None:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    assert cfg["change_admission"]["decision_outcomes"] == [
        "EXECUTE_NOW",
        "OBSERVE",
        "DO_NOT_CHANGE",
    ]
