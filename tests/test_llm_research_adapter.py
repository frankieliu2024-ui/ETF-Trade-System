import json
import os
from pathlib import Path

from scripts.llm_research_adapter import build_review_input, critique_review


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
