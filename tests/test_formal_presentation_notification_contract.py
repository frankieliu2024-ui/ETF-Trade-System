import hashlib
import json

from scripts import notification_center


def test_formal_presentation_projects_verbatim_without_bds_answer_validation(tmp_path, monkeypatch):
    body = "【ETF正式决策】2026-10-14 14:52 正式判断\n\n【业务结论】\n1. 原文。"
    envelope = {
        "request_id": "fd-current",
        "parent_request_id": "fd-current",
        "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
        "entry_mode": "MANUAL_SCHEDULED_TASK",
        "task_id": "ETF正式决策",
        "task_run_id": "run-current",
        "occurrence_id": "occ-current",
        "decision_id": "decision-current",
        "presentation_content": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "decision_response": {},
    }
    path = tmp_path / "fd-current__business_decision_source.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FORMAL_PRESENTATION_PATH", str(path))

    event = notification_center.formal_presentation_event()

    assert event is not None
    assert event["content"] == body
    assert event["content_hash"] == envelope["content_hash"]
    assert event["request_id"] == "fd-current"
    assert event["task_run_id"] == "run-current"


def test_formal_presentation_rejects_hash_mismatch_without_history_fallback(tmp_path, monkeypatch):
    envelope = {
        "request_id": "fd-current",
        "parent_request_id": "fd-current",
        "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
        "entry_mode": "MANUAL_CONVERSATION",
        "task_id": "ETF正式决策",
        "task_run_id": "run-current",
        "occurrence_id": "occ-current",
        "decision_id": "decision-current",
        "presentation_content": "当前正文",
        "content_hash": "old-hash",
    }
    path = tmp_path / "fd-current__business_decision_source.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FORMAL_PRESENTATION_PATH", str(path))

    assert notification_center.formal_presentation_event() is None


def test_manual_conversation_allows_empty_scheduled_identity_fields(tmp_path, monkeypatch):
    body = "【ETF正式决策】2026-10-14 21:38 正式判断\n\n【业务结论】\n\n1. 当前结论。"
    envelope = {
        "request_id": "fd-manual",
        "parent_request_id": "fd-manual",
        "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
        "entry_mode": "MANUAL_CONVERSATION",
        "task_id": "",
        "task_run_id": "",
        "occurrence_id": "",
        "decision_id": "decision-manual",
        "presentation_content": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    path = tmp_path / "fd-manual__business_decision_source.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FORMAL_PRESENTATION_PATH", str(path))

    event = notification_center.formal_presentation_event()

    assert event is not None
    assert event["content"] == body
    assert event["related_decision_id"] == "decision-manual"


def test_invalid_explicit_formal_path_does_not_fall_back_to_legacy_report(monkeypatch):
    monkeypatch.setenv("FORMAL_PRESENTATION_PATH", "/nonexistent/current-bds.json")
    monkeypatch.delenv("REVIEW_EVENT_PATH", raising=False)
    monkeypatch.delenv("REPORT_DELIVERY_PATH", raising=False)

    assert notification_center.report_delivery_event() is None


def test_formal_presentation_uses_fixed_title_and_separated_nested_items():
    body = (
        "【ETF正式决策】2026-10-14 21:38 正式判断\n\n"
        "【业务结论】\n\n"
        "1. 风险许可。\n\n"
        "2. 主候选。\n\n"
        "【观察ETF】\n\n"
        "1. 观察层。\n\n"
        "   1）恒生科技ETF（513180）：继续观察。\n\n"
        "   2）德国ETF（159561）：继续观察。"
    )
    assert body.startswith("【ETF正式决策】")
    assert "\n\n   1）" in body
    assert "\n\n   2）" in body
    assert "）" in body
