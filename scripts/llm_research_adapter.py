"""Provider-neutral, fail-open LLM adapter for read-only research assistance.

This module never writes formal state, never emits trade actions, and never logs
request headers, API keys, prompts, or raw provider responses.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CONFIG = ROOT / "config/research/llm_research_adapter.json"


@dataclass(frozen=True)
class AdapterConfig:
    enabled: bool
    provider: str
    base_url: str
    model: str
    timeout_seconds: int
    max_output_tokens: int
    max_retries: int
    max_calls_per_invocation: int
    daily_call_limit: int
    daily_budget_usd: float
    secret_env: str
    fail_open: bool
    persist_formal_state: bool
    allow_trade_action: bool


def load_config(path: Path = CONFIG) -> AdapterConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AdapterConfig(
        enabled=bool(raw.get("enabled", False)),
        provider=str(raw.get("provider", "deepseek")),
        base_url=str(raw.get("base_url", "")).rstrip("/"),
        model=str(raw.get("model", "")),
        timeout_seconds=max(1, min(int(raw.get("timeout_seconds", 20)), 120)),
        max_output_tokens=max(1, min(int(raw.get("max_output_tokens", 800)), 4000)),
        max_retries=max(0, min(int(raw.get("max_retries", 1)), 2)),
        max_calls_per_invocation=max(1, min(int(raw.get("max_calls_per_invocation", 1)), 1)),
        daily_call_limit=max(0, int(raw.get("daily_call_limit", 0))),
        daily_budget_usd=max(0.0, float(raw.get("daily_budget_usd", 0.0))),
        secret_env=str(raw.get("secret_env", "DEEPSEEK_API_KEY")),
        fail_open=bool(raw.get("fail_open", True)),
        persist_formal_state=bool(raw.get("persist_formal_state", False)),
        allow_trade_action=bool(raw.get("allow_trade_action", False)),
    )


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "SKIPPED",
        "provider": "",
        "model": "",
        "critique": None,
        "evidence_used": [],
        "uncertainties": [reason],
        "follow_ups": [],
        "production_action": None,
        "formal_state_write": False,
    }


def _validate_output(obj: Any, cfg: AdapterConfig) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("provider output is not an object")
    required = ("critique", "evidence_used", "uncertainties", "follow_ups")
    if any(k not in obj for k in required):
        raise ValueError("provider output schema is incomplete")
    if not isinstance(obj["critique"], str) or len(obj["critique"]) > 4000:
        raise ValueError("invalid critique")
    for key in ("evidence_used", "uncertainties", "follow_ups"):
        if not isinstance(obj[key], list) or len(obj[key]) > 20:
            raise ValueError("invalid list field")
        if not all(isinstance(x, str) and len(x) <= 500 for x in obj[key]):
            raise ValueError("invalid list item")
    return {
        "schema_version": "1.0",
        "status": "OK",
        "provider": cfg.provider,
        "model": cfg.model,
        "critique": obj["critique"],
        "evidence_used": obj["evidence_used"],
        "uncertainties": obj["uncertainties"],
        "follow_ups": obj["follow_ups"],
        "production_action": None,
        "formal_state_write": False,
    }


def _parse_json_content(content: str) -> Any:
    """Parse provider JSON without guessing at natural-language responses."""
    text = content.strip()
    if text.startswith("```json") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].strip().lower() == "```json" and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def _request(cfg: AdapterConfig, api_key: str, review: dict[str, Any]) -> dict[str, Any]:
    system = (
        "You are a research-only critique assistant for an ETF post-market review. "
        "Use only the supplied review facts. Do not invent facts. Do not give buy, sell, "
        "amount, permission, order, or portfolio instructions. Return JSON only with keys: "
        "critique (string), evidence_used (array of strings), uncertainties (array of strings), "
        "follow_ups (array of strings)."
    )
    body = json.dumps({
        "model": cfg.model,
        "temperature": 0,
        "max_tokens": cfg.max_output_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(review, ensure_ascii=False, separators=(",", ":"))},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg.base_url + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    return _validate_output(_parse_json_content(content), cfg)


def critique_review(review: dict[str, Any], *, config_path: Path = CONFIG, enable_once: bool = False) -> dict[str, Any]:
    cfg = load_config(config_path)
    if enable_once:
        cfg = replace(cfg, enabled=True)
    if not cfg.enabled:
        return _blocked("adapter_disabled")
    if cfg.persist_formal_state or cfg.allow_trade_action:
        return _blocked("unsafe_configuration")
    if cfg.daily_call_limit <= 0 or cfg.daily_budget_usd <= 0:
        return _blocked("cost_guard_zero")
    # The caller may supply a trusted count from the existing job/orchestration
    # context. Missing or malformed count fails open and skips the optional call.
    try:
        calls_today = int(os.environ.get("LLM_CALLS_TODAY", "0"))
    except ValueError:
        return _blocked("invalid_daily_counter")
    if calls_today >= cfg.daily_call_limit:
        return _blocked("daily_call_limit")
    api_key = os.environ.get(cfg.secret_env, "")
    if not api_key:
        return _blocked("secret_unavailable")
    last_error = "provider_unavailable"
    single_call = os.environ.get("LLM_SINGLE_CALL", "").lower() in {"1", "true", "yes"}
    retries = 0 if single_call else cfg.max_retries
    for attempt in range(retries + 1):
        try:
            result = _request(cfg, api_key, review)
            result["call_count"] = 1
            return result
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
            if attempt < cfg.max_retries:
                time.sleep(0.2)
    return _blocked(last_error)


def build_review_input(review: dict[str, Any]) -> dict[str, Any]:
    """Map only research-safe facts from the frozen formal review payload."""
    source = review.get("review") if isinstance(review.get("review"), dict) else review
    return {
        "market_date": source.get("market_date", review.get("market_date", "")),
        "review_version": source.get("review_version", ""),
        "reviewed_at_beijing": source.get("reviewed_at_beijing", ""),
        "opportunity_status": source.get("opportunity_status", ""),
        "main_candidate": source.get("main_candidate", ""),
        "research_summary": {
            "today_summary": source.get("today_summary", ""),
            "judgment_quality": source.get("judgment_quality", ""),
            "judgment_quality_reason": source.get("judgment_quality_reason", ""),
            "execution_quality": source.get("execution_quality", ""),
            "execution_quality_reason": source.get("execution_quality_reason", ""),
            "capital_efficiency": source.get("capital_efficiency", ""),
            "capital_efficiency_reason": source.get("capital_efficiency_reason", ""),
            "next_validation": source.get("next_validation", []),
            "max_risk": source.get("max_risk", ""),
            "error_reason": source.get("error_reason", ""),
            "information_gap": source.get("information_gap", ""),
            "most_fragile_hypothesis": source.get("most_fragile_hypothesis", ""),
            "overseas_overestimate": source.get("overseas_overestimate", ""),
            "overseas_a_share_divergence": source.get("overseas_a_share_divergence", ""),
        },
        "review_boundary": source.get("review_boundary", ""),
    }
