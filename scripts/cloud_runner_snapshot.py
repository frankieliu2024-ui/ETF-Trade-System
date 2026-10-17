from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from state_manager import atomic_json_write, read_current, update_current
from market_data_guard import classify_provider_failure, validate_market_row
from tencent_quote import fetch_tencent_quotes

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
UTC = timezone.utc


def load_runtime_policy() -> dict:
    path = ROOT / "config" / "runtime_policy.json"
    default = {
        "target_cadence_seconds": 600,
        "fresh_max_age_seconds": 900,
        "degraded_max_age_seconds": 1500,
        "stale_after_seconds": 1500,
        "close_grace_seconds": 900,
        "provider_timeout_seconds": 25,
        "provider_retry_limit": 2,
        "provider_max_workers": 3,
    }
    if path.exists():
        default.update(json.loads(path.read_text(encoding="utf-8")))
    return default


def load_etf_universe() -> list[tuple[str, str]]:
    path = ROOT / "config" / "market" / "etf_monitor_universe.json"
    if not path.exists():
        raise RuntimeError("missing canonical ETF universe: config/market/etf_monitor_universe.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    objects = data.get("objects") or []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in objects:
        code = str(item.get("code", "")).strip()
        thscode = str(item.get("thscode", "")).strip()
        if not code or not thscode:
            raise RuntimeError("ETF universe contains missing code/thscode")
        if code in seen:
            raise RuntimeError(f"duplicate ETF code in universe: {code}")
        seen.add(code)
        result.append((code, thscode))
    # Account truth is the mandatory runtime overlay for actual holdings.
    # The persisted universe remains the canonical Holding+Observation identity
    # projection, but a delayed projection/replay must never make a positive
    # current holding disappear from the market acquisition target.
    account_path = ROOT / "data" / "state" / "account_fact.json"
    if account_path.exists():
        account = json.loads(account_path.read_text(encoding="utf-8"))
        for pos in account.get("positions") or []:
            if not isinstance(pos, dict):
                continue
            if str(pos.get("asset_type") or "").upper() != "ETF":
                continue
            try:
                quantity = float(pos.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0.0
            code = str(pos.get("code") or pos.get("symbol") or "").strip()
            if quantity <= 0 or not code or code in seen:
                continue
            thscode = str(pos.get("thscode") or "").strip()
            if not thscode and code.isdigit() and len(code) == 6:
                thscode = code + (".SH" if code.startswith(("5", "6")) else ".SZ")
            if not thscode:
                raise RuntimeError(f"active held ETF missing resolvable thscode: {code}")
            seen.add(code)
            result.append((code, thscode))
    if not result:
        raise RuntimeError("canonical ETF universe is empty")
    return result


POLICY = load_runtime_policy()
ETF = load_etf_universe()


def load_eastmoney_fallback_etfs() -> set[str]:
    """Return A-share ETF codes with an effective direct fallback policy.

    The policy resolver applies the canonical default to newly added monitored
    holdings/observations and explicit overrides to verified exceptions.
    """
    configured: set[str] = set()
    path = ROOT / "config" / "market" / "provider_priority.json"
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        for code, thscode in ETF:
            policy = resolve_object_policy(config, thscode, asset_class="ETF", code=code)
            if (
                isinstance(policy, dict)
                and str(policy.get("primary") or "") == "tencent_qq"
                and "eastmoney_push2" in (policy.get("fallback") or [])
                and policy.get("direct_only") is True
            ):
                configured.add(str(code))
    return configured

