import json

import scripts.llm_research_adapter as adapter
from scripts.llm_research_adapter import run_json_research_task


class _FakeResponse:
    status = 200

    def __init__(self, content):
        self._body = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": content}}]
        }).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def _cfg(tmp_path, **overrides):
    raw = {
        "enabled": True,
        "provider": "deepseek",
        "base_url": "https://example.invalid",
        "model": "deepseek-chat",
        "daily_call_limit": 20,
        "daily_budget_usd": 0.5,
        "secret_env": "TEST_LLM_KEY",
        "max_retries": 0,
        "persist_formal_state": False,
        "allow_trade_action": False,
    }
    raw.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _validator(obj, cfg):
    if set(obj) != {"value"} or not isinstance(obj["value"], str):
        raise ValueError("invalid test schema")
    return {
        "schema_version": "1.0",
        "status": "OK",
        "provider": cfg.provider,
        "model": cfg.model,
        "result": obj,
        "uncertainties": [],
        "production_action": None,
        "formal_state_write": False,
    }


def test_generic_task_is_fail_open_when_disabled(tmp_path):
    result = run_json_research_task(
        "system", {}, _validator, config_path=_cfg(tmp_path, enabled=False)
    )
    assert result["status"] == "SKIPPED"
    assert result["uncertainties"] == ["adapter_disabled"]
    assert result["formal_state_write"] is False
    assert result["production_action"] is None


def test_generic_task_rejects_unsafe_configuration(tmp_path):
    result = run_json_research_task(
        "system", {}, _validator,
        config_path=_cfg(tmp_path, allow_trade_action=True),
        enable_once=True,
    )
    assert result["status"] == "SKIPPED"
    assert result["uncertainties"] == ["unsafe_configuration"]


def test_generic_task_uses_same_single_call_guard_and_redacted_observability(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "redacted-test-only")
    monkeypatch.setenv("LLM_SINGLE_CALL", "1")
    monkeypatch.setattr(
        adapter.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeResponse(json.dumps({"value": "ok"})),
    )
    result = run_json_research_task(
        "system", {"safe": True}, _validator,
        config_path=_cfg(tmp_path),
        enable_once=True,
        observability=True,
    )
    assert result["status"] == "OK"
    assert result["call_count"] == 1
    assert result["result"] == {"value": "ok"}
    shape = result["response_observability"]
    assert shape["request_success"] is True
    assert shape["starts_with_object"] is True
    assert "content" not in shape
