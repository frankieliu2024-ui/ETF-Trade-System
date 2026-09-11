import json
import os
from pathlib import Path

from scripts.llm_research_adapter import _parse_json_content, build_review_input, critique_review


def test_disabled_adapter_is_fail_open(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "enabled": False,
        "daily_call_limit": 20,
        "daily_budget_usd": 0.5,
        "secret_env": "TEST_LLM_KEY",
    }), encoding="utf-8")
    result = critique_review({"market_date": "2026-09-11"}, config_path=cfg)
    assert result["status"] == "SKIPPED"
    assert result["uncertainties"] == ["adapter_disabled"]


def test_missing_secret_is_fail_open(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "enabled": True,
        "provider": "deepseek",
        "base_url": "https://example.invalid",
        "model": "deepseek-chat",
        "daily_call_limit": 20,
        "daily_budget_usd": 0.5,
        "secret_env": "TEST_LLM_KEY",
        "fail_open": True,
    }), encoding="utf-8")
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)
    result = critique_review({"market_date": "2026-09-11"}, config_path=cfg)
    assert result["status"] == "SKIPPED"
    assert result["uncertainties"] == ["secret_unavailable"]


def test_daily_guard_is_fail_open(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "enabled": True,
        "provider": "deepseek",
        "base_url": "https://example.invalid",
        "model": "deepseek-chat",
        "daily_call_limit": 1,
        "daily_budget_usd": 0.5,
        "secret_env": "TEST_LLM_KEY",
        "fail_open": True,
    }), encoding="utf-8")
    monkeypatch.setenv("TEST_LLM_KEY", "redacted-test-only")
    monkeypatch.setenv("LLM_CALLS_TODAY", "1")
    result = critique_review({"market_date": "2026-09-11"}, config_path=cfg)
    assert result["status"] == "SKIPPED"
    assert result["uncertainties"] == ["daily_call_limit"]


def test_input_is_allowlisted_and_actions_are_excluded():
    result = build_review_input({
        "market_date": "2026-09-11",
        "daily_market_summary": {"latest_valid_node": "1500"},
        "account_fact": {"cash": 999999},
        "trade_action": "BUY",
        "review_boundary": "read only",
    })
    assert result["market_date"] == "2026-09-11"
    assert "account_fact" not in result
    assert "trade_action" not in result


def test_output_contract_rejects_action_fields(tmp_path, monkeypatch):
    # Configuration itself rejects any attempt to turn the adapter into a
    # formal state writer or action generator.
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "enabled": True,
        "provider": "deepseek",
        "base_url": "https://example.invalid",
        "model": "deepseek-chat",
        "daily_call_limit": 20,
        "daily_budget_usd": 0.5,
        "secret_env": "TEST_LLM_KEY",
        "persist_formal_state": True,
    }), encoding="utf-8")
    result = critique_review({}, config_path=cfg)
    assert result["status"] == "SKIPPED"
    assert result["uncertainties"] == ["unsafe_configuration"]


def test_single_call_mode_disables_retries(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"enabled": True, "provider": "deepseek", "base_url": "https://example.invalid", "model": "deepseek-chat", "daily_call_limit": 20, "daily_budget_usd": 0.5, "secret_env": "TEST_LLM_KEY", "max_retries": 2}), encoding="utf-8")
    calls = {"count": 0}
    def fake_request(cfg, key, review):
        calls["count"] += 1
        raise OSError("test provider failure")
    monkeypatch.setenv("TEST_LLM_KEY", "redacted-test-only")
    monkeypatch.setenv("LLM_SINGLE_CALL", "1")
    monkeypatch.setattr("scripts.llm_research_adapter._request", fake_request)
    result = critique_review({}, config_path=cfg)
    assert result["status"] == "SKIPPED"
    assert calls["count"] == 1


def test_enable_once_overrides_only_call_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "enabled": False, "provider": "deepseek", "base_url": "https://example.invalid",
        "model": "deepseek-chat", "daily_call_limit": 20, "daily_budget_usd": 0.5,
        "secret_env": "TEST_LLM_KEY",
    }), encoding="utf-8")
    monkeypatch.setenv("TEST_LLM_KEY", "redacted-test-only")
    monkeypatch.setattr("scripts.llm_research_adapter._request", lambda cfg, key, review: {
        "schema_version": "1.0", "status": "OK", "provider": cfg.provider,
        "model": cfg.model, "critique": "ok", "evidence_used": [],
        "uncertainties": [], "follow_ups": [], "production_action": None,
        "formal_state_write": False,
    })
    result = critique_review({}, config_path=cfg, enable_once=True)
    assert result["status"] == "OK"
    assert result["formal_state_write"] is False


def test_frozen_review_nested_mapping_is_nonempty_and_safe():
    frozen = {
        "event_type": "FORMAL_POST_CLOSE_REVIEW",
        "market_date": "2026-09-11",
        "review": {
            "market_date": "2026-09-11",
            "review_version": "V2.2.31_CLOSE_REVIEW",
            "reviewed_at_beijing": "2026-09-11T20:30:46+08:00",
            "opportunity_status": "无机会",
            "main_candidate": "现金",
            "today_summary": "risk-off review",
            "judgment_quality": "良好",
            "judgment_quality_reason": "evidence-based",
            "execution_quality": "合格",
            "capital_efficiency": "改善",
            "next_validation": ["跨日承接"],
            "max_risk": "共同因子风险",
            "review_boundary": "read only",
            "holding_actions": {"ETF": "BUY"},
            "amount_yuan": 1000,
            "risk_permission": "允许新增",
        },
    }
    result = build_review_input(frozen)
    assert result["market_date"] == "2026-09-11"
    assert result["research_summary"]["today_summary"] == "risk-off review"
    assert result["research_summary"]["next_validation"] == ["跨日承接"]
    assert len(json.dumps(result, ensure_ascii=False)) > 100
    assert "holding_actions" not in result
    assert "amount_yuan" not in result
    assert "risk_permission" not in result


def test_json_content_parser_accepts_bare_json_and_complete_code_fence():
    payload = '{"critique":"ok","evidence_used":[],"uncertainties":[],"follow_ups":[]}'
    assert _parse_json_content(payload)["critique"] == "ok"
    assert _parse_json_content("```json
" + payload + "
```")["critique"] == "ok"


def test_invalid_or_truncated_json_is_not_salvaged():
    for content in ("not json", '{"critique":'):
        try:
            _parse_json_content(content)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("invalid JSON must remain rejected")


def test_json_decode_error_remains_fail_open(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "enabled": True, "provider": "deepseek", "base_url": "https://example.invalid",
        "model": "deepseek-chat", "daily_call_limit": 20, "daily_budget_usd": 0.5,
        "secret_env": "TEST_LLM_KEY", "max_retries": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("TEST_LLM_KEY", "redacted-test-only")
    monkeypatch.setenv("LLM_SINGLE_CALL", "1")
    monkeypatch.setattr(
        "scripts.llm_research_adapter._request",
        lambda cfg, key, review: (_ for _ in ()).throw(json.JSONDecodeError("bad", "", 0)),
    )
    result = critique_review({}, config_path=cfg, enable_once=True)
    assert result["status"] == "SKIPPED"
    assert result["uncertainties"] == ["JSONDecodeError"]
