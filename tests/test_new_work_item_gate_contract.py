from __future__ import annotations

import json
from pathlib import Path

from scripts.check_production_mutation_protocol import classify_new_work_item_gate, run

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/生产变更与并发写入协议_V1.0.md"
CFG = ROOT / "config/maintenance/production_mutation_protocol.json"


def test_gate_machine_contract_and_protocol_check() -> None:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    gate = cfg["change_admission"]["new_work_item_gate"]
    assert set(gate["fields"]) == {"ACTION_IMPACT", "EXISTING_MECHANISM_INSUFFICIENT", "EVIDENCE_QUALITY"}
    assert gate["outcomes"]["NEW_WORK_ITEM_GATE"] == ["PASS", "FAIL"]
    assert gate["outcomes"]["NEW_ISSUE"] == ["YES", "NO"]
    assert cfg["change_admission"]["decision_outcomes"] == ["EXECUTE_NOW", "OBSERVE", "DO_NOT_CHANGE"]
    result = run(ROOT)
    assert result["status"] == "PASS", result["errors"]


def test_duplicate_and_low_evidence_do_not_create_issue() -> None:
    duplicate = classify_new_work_item_gate({"duplicate_or_reusable_item": True, "ACTION_IMPACT": "YES", "EXISTING_MECHANISM_INSUFFICIENT": "YES", "EVIDENCE_QUALITY": "SUFFICIENT"})
    assert duplicate["NEW_WORK_ITEM_GATE"] == "FAIL" and duplicate["NEW_ISSUE"] == "NO"
    low_evidence = classify_new_work_item_gate({"ACTION_IMPACT": "YES", "EXISTING_MECHANISM_INSUFFICIENT": "YES", "EVIDENCE_QUALITY": "INSUFFICIENT"})
    assert low_evidence["NEW_WORK_ITEM_GATE"] == "FAIL" and low_evidence["NEW_ISSUE"] == "NO"


def test_material_repeated_defect_can_pass_and_emergency_is_not_blocked() -> None:
    passed = classify_new_work_item_gate({"ACTION_IMPACT": "YES", "EXISTING_MECHANISM_INSUFFICIENT": "YES", "EVIDENCE_QUALITY": "SUFFICIENT"})
    assert passed["NEW_WORK_ITEM_GATE"] == "PASS" and passed["NEW_ISSUE"] == "YES"
    emergency = classify_new_work_item_gate({"emergency_production_failure": True, "ACTION_IMPACT": "INCONCLUSIVE", "EXISTING_MECHANISM_INSUFFICIENT": "INCONCLUSIVE", "EVIDENCE_QUALITY": "INSUFFICIENT"})
    assert emergency["NEW_WORK_ITEM_GATE"] == "PASS" and emergency["NEW_ISSUE"] == "YES"


def test_protocol_documents_state_the_gate_without_a_second_rule_source() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "NEW_WORK_ITEM_GATE" in doc
    assert "不得使用隐藏综合评分、固定权重或固定次数阈值" in doc
    assert "与该风险匹配的一次真实生产暴露窗口" in doc


def test_execution_control_plane_is_executor_agnostic() -> None:
    doc = (ROOT / "docs/Codex协作执行说明.md").read_text(encoding="utf-8")
    assert "CHATGPT_CHAT" in doc
    assert "CHATGPT_WORK" in doc
    assert "CODEX" in doc
    assert "执行器选择只影响“由谁执行”" in doc
    assert "不强制转Codex" in doc
    assert "BRIEF／PACKET分别由任务复杂度决定，不由执行器决定" in doc
    assert "不得自动合并" in doc
