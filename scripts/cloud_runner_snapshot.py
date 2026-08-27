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
    if not result:
        raise RuntimeError("canonical ETF universe is empty")
    return result


POLICY = load_runtime_policy()
ETF = load_etf_universe()


def load_eastmoney_fallback_etfs() -> set[str]:
    path = ROOT / "config" / "market" / "provider_priority.json"
    if not path.exists():
        return set()
    config = json.loads(path.read_text(encoding="utf-8"))
    policy = config.get("object_fallback_policy") or {}
    return {
        str(thscode).split(".")[0]
        for thscode, rule in policy.items()
        if str(rule.get("primary", "")) == "tencent_qq"
        and "eastmoney_push2" in (rule.get("fallback") or [])
        and rule.get("direct_only") is True
    }


EASTMONEY_FALLBACK_ETFS = load_eastmoney_fallback_etfs()


def load_eastmoney_fallback_indices() -> set[str]:
    path = ROOT / "config" / "market" / "provider_priority.json"
    if not path.exists():
        return set()
    config = json.loads(path.read_text(encoding="utf-8"))
    policy = config.get("object_fallback_policy") or {}
    return {str(thscode).split(".")[0] for thscode, rule in policy.items() if "eastmoney_push2" in (rule.get("fallback") or []) and rule.get("direct_only") is True and str(thscode).upper().endswith((".SH", ".SZ")) and str(thscode).split(".")[0] in {"000001", "399006"}}


EASTMONEY_FALLBACK_INDICES = load_eastmoney_fallback_indices()
RETRY_LIMIT = int(os.environ.get("HITHINK_RETRY_LIMIT", POLICY["provider_retry_limit"]))
TIMEOUT_SECONDS = int(os.environ.get("HITHINK_TIMEOUT_SECONDS", POLICY["provider_timeout_seconds"]))
MAX_WORKERS = int(os.environ.get("HITHINK_MAX_WORKERS", POLICY["provider_max_workers"]))

# Run-local circuit breaker: bypass a transiently unavailable primary for the
# remainder of this run, without persisting a provider disable decision.
PRIMARY_RUN_BYPASS = threading.Event()
CLOSE_GRACE_SECONDS = int(POLICY.get("close_grace_seconds", 900))

INDEX = [("000001", "000001.SH"), ("399006", "399006.SZ"), ("000688", "000688.SH")]
NODES = {"auction", "0925", "1030", "1130", "1330", "1430", "close", "live", "manual", "scheduled"}
PLANNED_TIMES = {"auction": "09:15-09:25", "0925": "09:25", "1030": "10:30", "1130": "11:30", "1330": "13:30", "1430": "14:30", "close": "15:00"}


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def provider_as_of_beijing(timestamp_ms: object) -> str:
    if timestamp_ms in (None, ""):
        return ""
    return datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=SHANGHAI).isoformat(timespec="seconds")


def a_share_market_phase(captured_dt: datetime) -> str:
    minute = captured_dt.hour * 60 + captured_dt.minute
    # 09:25-09:29 is still pre-continuous trading. A delayed scheduled runner
    # may capture the final opening-auction result here; keep auction semantics.
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return "OPENING_CALL_AUCTION"
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        return "CONTINUOUS_MORNING"
    if 13 * 60 <= minute < 14 * 60 + 57:
        return "CONTINUOUS_AFTERNOON"
    if 14 * 60 + 57 <= minute <= 15 * 60:
        return "CLOSING_CALL_AUCTION"
    if 15 * 60 < minute <= 15 * 60 + max(1, CLOSE_GRACE_SECONDS // 60):
        return "POST_CLOSE_GRACE"
    return "OUTSIDE_SESSION"


def in_a_share_capture_window(captured_dt: datetime) -> bool:
    if captured_dt.weekday() >= 5:
        return False
    minute = captured_dt.hour * 60 + captured_dt.minute
    opening_auction = 9 * 60 + 15 <= minute < 9 * 60 + 30
    morning = 9 * 60 + 30 <= minute <= 11 * 60 + 30
    afternoon = 13 * 60 <= minute <= 15 * 60
    close_grace_end = 15 * 60 + max(1, CLOSE_GRACE_SECONDS // 60)
    close_grace = 15 * 60 < minute <= close_grace_end
    return opening_auction or morning or afternoon or close_grace


def resolve_scheduled_node(captured_dt: datetime) -> str:
    minute = captured_dt.hour * 60 + captured_dt.minute
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return "auction"
    return "close" if minute >= 15 * 60 else "live"


def close_already_recorded(market_date: str) -> bool:
    current = read_current(ROOT)
    return current.get("market_date") == market_date and current.get("latest_valid_node") == "close" and current.get("node_status") == "READY"


def cli_path() -> str:
    found = shutil.which("hithink-finance")
    if found:
        return found
    raise RuntimeError("hithink-finance CLI not found on PATH")


def write_runtime_health(payload: dict) -> None:
    base = {
        "generated_at": now_utc_text(),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "target_cadence_seconds": POLICY["target_cadence_seconds"],
        "fresh_max_age_seconds": POLICY["fresh_max_age_seconds"],
        "degraded_max_age_seconds": POLICY["degraded_max_age_seconds"],
        "close_grace_seconds": CLOSE_GRACE_SECONDS,
        "provider_timeout_seconds": TIMEOUT_SECONDS,
        "provider_retry_limit": RETRY_LIMIT,
        "provider_max_workers": MAX_WORKERS,
        "etf_universe_count": len(ETF),
    }
    base.update(payload)
    atomic_json_write(ROOT / "data" / "state" / "runtime_health.json", base)


def run_json(cli: str, args: list[str], raw_path: Path) -> dict:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            completed = subprocess.run(
                [cli, *args, "--output", str(raw_path), "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            last_error = f"timeout after {TIMEOUT_SECONDS}s"
        else:
            if completed.returncode == 0 and raw_path.exists():
                obj = json.loads(raw_path.read_text(encoding="utf-8"))
                if obj.get("ok") and obj.get("meta", {}).get("source") == "remote":
                    return obj
                last_error = f"business failure meta={obj.get('meta')}"
            else:
                last_error = (completed.stderr or "CLI failed")[-500:]
        failure_class = classify_provider_failure(last_error)
        if failure_class == "STRUCTURAL":
            break
        if failure_class == "TRANSIENT":
            PRIMARY_RUN_BYPASS.set()
            break
        if attempt < RETRY_LIMIT:
            time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(f"request failed after {RETRY_LIMIT} attempts: {args}; {last_error}")


def row(asset_class: str, code: str, thscode: str, item: dict, captured: str, provider_ts: object, market_phase: str, *, provider: str = "hithink-finance", provider_primary: str = "hithink-finance", fallback_used: bool = False, fallback_reason: str = "") -> dict:
    required = ["open_price", "high_price", "low_price", "last_price", "volume", "turnover"]
    missing = [key for key in required if item.get(key) is None]
    auction_partial = (
        market_phase == "OPENING_CALL_AUCTION"
        and bool(missing)
        and set(missing).issubset({"open_price", "high_price", "low_price", "volume", "turnover"})
        and item.get("last_price") is not None
        and item.get("prev_price") is not None
        and provider_ts not in (None, "")
    )
    no_trade_partial = (
        market_phase in {"CONTINUOUS_MORNING", "CONTINUOUS_AFTERNOON", "CLOSING_CALL_AUCTION"}
        and bool(missing)
        and set(missing).issubset({"open_price", "high_price", "low_price", "volume", "turnover"})
        and item.get("last_price") is not None
        and item.get("prev_price") is not None
        and provider_ts not in (None, "")
    )
    explicit_halt = (
        str(item.get("trading_status") or "").upper() in {"SUSPENDED", "HALTED"}
        and item.get("last_price") is not None
        and item.get("prev_price") is not None
        and provider_ts not in (None, "")
    )
    if missing and not (auction_partial or no_trade_partial or explicit_halt):
        raise RuntimeError(f"{code} missing fields: {','.join(missing)}")
    if not (auction_partial or no_trade_partial or explicit_halt) and (
        item["high_price"] < max(item["open_price"], item["last_price"])
        or item["low_price"] > min(item["open_price"], item["last_price"])
    ):
        raise RuntimeError(f"{code} failed OHLC relationship")
    quality_status = "PASS" if explicit_halt else ("DEGRADED" if (auction_partial or no_trade_partial) else "PASS")
    semantic_note = (
        "provider明确证券当前停牌/暂停交易；最新价仅为最后有效参考价，保留真实provider时点，不伪造OHLC，不按可成交价格解释。"
        if explicit_halt
        else ("OPENING_CALL_AUCTION阶段provider尚未形成完整日内OHLC；保留最新价、昨收、成交量、成交额和provider时点，禁止用旧OHLC补齐。"
        if auction_partial
        else ("交易中provider仅返回最新价和昨收，未产生可用新成交量/OHLC；保留事实并标记为延迟/无新成交，不用旧OHLC补齐。" if no_trade_partial else ("OPENING_CALL_AUCTION阶段仅按集合竞价时点快照解释，不与连续竞价最新成交语义混用。" if market_phase == "OPENING_CALL_AUCTION" else "")))
    )
    return {
        "asset_class": asset_class, "symbol": code, "thscode": thscode,
        "open": item.get("open_price"), "high": item.get("high_price"),
        "low": item.get("low_price"), "close": item.get("last_price"),
        "prev_close": item.get("prev_price"),
        "change_pct": item.get("price_change_ratio_pct"),
        "volume": item["volume"], "amount": item["turnover"],
        "provider": provider, "provider_primary": provider_primary, "provider_used": provider,
        "fallback_used": fallback_used, "fallback_reason": fallback_reason,
        "provider_timestamp_ms": provider_ts,
        "as_of_beijing": provider_as_of_beijing(provider_ts),
        "captured_at": captured, "captured_at_beijing": captured, "timezone": "Asia/Shanghai",
        "market_phase": market_phase,
        "quality_status": quality_status,
        "semantic_note": semantic_note,
        "trading_status": item.get("trading_status", "TRADING"),
        "tradable": item.get("tradable", True),
        "provider_status_code": item.get("provider_status_code", ""),
    }


def eastmoney_timestamp_ms(value: object) -> int:
    if value in (None, "", "-", 0, "0"):
        raise RuntimeError("Eastmoney response has no provider timestamp")
    number = float(value)
    if number < 10_000_000_000:
        number *= 1000
    return int(number)


def fetch_eastmoney_index(code: str, thscode: str, market_phase: str) -> dict:
    if code not in EASTMONEY_FALLBACK_INDICES:
        raise RuntimeError(f"Eastmoney index fallback is not enabled for {code}")
    params = {"secid": f"{'1' if thscode.endswith('.SH') else '0'}.{code}", "fltt": "2", "invt": "2", "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f124"}
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.15"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    data = payload.get("data") or {}
    if str(data.get("f57", "")) != code:
        raise RuntimeError(f"Eastmoney index code mismatch for {code}: {data.get('f57')}")
    fields = {"open_price": data.get("f46"), "high_price": data.get("f44"), "low_price": data.get("f45"), "last_price": data.get("f43"), "prev_price": data.get("f60"), "volume": int(float(data.get("f47")) * 100) if data.get("f47") not in (None, "", "-") else None, "turnover": data.get("f48")}
    if any(value in (None, "", "-") for value in fields.values()):
        raise RuntimeError(f"Eastmoney index {code} missing direct fields")
    provider_ts = eastmoney_timestamp_ms(data.get("f86"))
    provider_dt = datetime.fromtimestamp(provider_ts / 1000, tz=SHANGHAI)
    now_dt = now_shanghai()
    if provider_dt.date() != now_dt.date() or abs((now_dt - provider_dt).total_seconds()) > max(60, int(POLICY["degraded_max_age_seconds"])):
        raise RuntimeError(f"Eastmoney index {code} provider timestamp is stale: {provider_dt.isoformat()}")
    item = {**fields, "price_change_ratio_pct": ((fields["last_price"] / fields["prev_price"]) - 1) * 100 if fields["prev_price"] else None}
    candidate = row("A_SHARE_INDEX", code, thscode, item, now_dt.isoformat(timespec="seconds"), provider_ts, market_phase, provider="eastmoney_push2", provider_primary="hithink-finance", fallback_used=True, fallback_reason="hithink-finance index snapshot unavailable or invalid; verified direct Eastmoney quote") | {"provider_symbol": f"{'1' if thscode.endswith('.SH') else '0'}.{code}", "provider_timestamp_field": "f86", "volume_raw": data.get("f47"), "volume_unit_raw": "hand", "volume_unit": "share", "amount_raw": data.get("f48"), "amount_unit": "CNY", "provider_name": str(data.get("f58", ""))}
    ok, reason = validate_market_row(candidate, code, expected_name=candidate.get("provider_name"), market_date=now_dt.date().isoformat(), now=now_dt.astimezone(UTC), runtime_policy=POLICY)
    if not ok:
        raise RuntimeError(f"{code} quality guard: {reason}")
    return candidate


def failed_index_row(code: str, thscode: str, error: str, market_phase: str) -> dict:
    captured = now_shanghai().isoformat(timespec="seconds")
    return {"asset_class": "A_SHARE_INDEX", "symbol": code, "thscode": thscode, "open": None, "high": None, "low": None, "close": None, "prev_close": None, "change_pct": None, "volume": None, "amount": None, "provider": "hithink-finance", "provider_primary": "hithink-finance", "provider_used": "hithink-finance", "fallback_used": False, "fallback_reason": "", "provider_timestamp_ms": None, "as_of_beijing": "", "captured_at": captured, "captured_at_beijing": captured, "timezone": "Asia/Shanghai", "market_phase": market_phase, "quality_status": "FAILED", "error": error[-500:], "failure_class": classify_provider_failure(error), "semantic_note": "指数直接行情失败；未使用代理或旧行情补齐。"}


def fetch_tencent_a_share(asset_class: str, code: str, thscode: str, market_phase: str) -> dict:
    quote = fetch_tencent_quotes([thscode], timeout=min(TIMEOUT_SECONDS, 10))[str(thscode).upper()]
    received = now_shanghai()
    candidate = row(
        asset_class, code, thscode, quote, received.isoformat(timespec="seconds"),
        quote["provider_timestamp_ms"], market_phase,
        provider="tencent_qq", provider_primary="tencent_qq",
    )
    candidate.update({
        "provider_symbol": quote["provider_symbol"],
        "provider_timestamp_field": "Tencent field 30",
        "volume_raw": quote.get("volume"),
        "volume_unit": "share",
        "amount_raw": quote.get("turnover"),
        "amount_unit": "CNY",
        "provider_name": quote.get("name"),
        "provider_price_change_amount": quote.get("provider_price_change_amount"),
        "provider_price_change_ratio_pct": quote.get("provider_price_change_ratio_pct"),
        "change_pct_source": quote.get("change_pct_source"),
        "provider_field_31_semantics": quote.get("provider_field_31_semantics"),
        "provider_field_32_semantics": quote.get("provider_field_32_semantics"),
    })
    ok, reason = validate_market_row(
        candidate, code, expected_name=quote.get("name"),
        market_date=received.date().isoformat(),
        now=received.astimezone(UTC), runtime_policy=POLICY,
    )
    if not ok:
        raise RuntimeError(f"Tencent {thscode} quality guard: {reason}")
    return candidate


def fetch_eastmoney_etf(code: str, thscode: str, market_phase: str) -> dict:
    if code not in EASTMONEY_FALLBACK_ETFS:
        raise RuntimeError(f"Eastmoney ETF fallback is not enabled for {code}")
    params = {
        "secid": f"{'1' if thscode.endswith('.SH') else '0'}.{code}",
        "fltt": "2",
        "invt": "2",
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f124",
    }
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.15"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        status_code = int(response.status)
        payload = json.load(response)
    data = payload.get("data") or {}
    if str(data.get("f57", "")) != code:
        raise RuntimeError(f"Eastmoney code mismatch for {code}: {data.get('f57')}")
    if not str(data.get("f58", "")).strip():
        raise RuntimeError(f"Eastmoney {code} missing security name")
    volume_raw = data.get("f47")
    amount_raw = data.get("f48")
    fields = {
        "open_price": data.get("f46"),
        "high_price": data.get("f44"),
        "low_price": data.get("f45"),
        "last_price": data.get("f43"),
        "prev_price": data.get("f60"),
        "volume": int(float(volume_raw) * 100) if volume_raw not in (None, "", "-") else None,
        "turnover": amount_raw,
    }
    provider_ts = eastmoney_timestamp_ms(data.get("f86"))
    no_trade_partial = (
        fields["last_price"] not in (None, "", "-")
        and fields["prev_price"] not in (None, "", "-")
        and provider_ts > 0
        and all(fields[key] in (None, "", "-") for key in ("open_price", "high_price", "low_price", "volume", "turnover"))
    )
    if any(value in (None, "", "-") for value in fields.values()) and not no_trade_partial:
        raise RuntimeError(f"Eastmoney {code} missing direct ETF fields")
    if fields["volume"] not in (None, "", "-") and fields["volume"] < 0 or amount_raw not in (None, "", "-") and float(amount_raw) < 0:
        raise RuntimeError(f"Eastmoney {code} has negative volume or amount")
    provider_ts = eastmoney_timestamp_ms(data.get("f86"))
    provider_dt = datetime.fromtimestamp(provider_ts / 1000, tz=SHANGHAI)
    now_dt = now_shanghai()
    if provider_dt.date() != now_dt.date():
        raise RuntimeError(f"Eastmoney {code} provider date is stale: {provider_dt.isoformat()}")
    if abs((now_dt - provider_dt).total_seconds()) > max(60, int(POLICY["degraded_max_age_seconds"])):
        raise RuntimeError(f"Eastmoney {code} provider timestamp is stale: {provider_dt.isoformat()}")
    if not no_trade_partial and (fields["high_price"] < max(fields["open_price"], fields["last_price"]) or fields["low_price"] > min(fields["open_price"], fields["last_price"])):
        raise RuntimeError(f"Eastmoney {code} failed OHLC relationship")
    item = {
        **fields,
        "price_change_ratio_pct": ((fields["last_price"] / fields["prev_price"]) - 1) * 100 if fields["prev_price"] else None,
    }
    candidate = row(
        "ETF", code, thscode, item, now_dt.isoformat(timespec="seconds"), provider_ts, market_phase,
        provider="eastmoney_push2", provider_primary="tencent_qq",
        fallback_used=True,
        fallback_reason="Tencent primary and Hithink fallback unavailable; verified direct Eastmoney push2 ETF quote",
    ) | {
        "provider_http_status": status_code,
        "provider_symbol": f"{'1' if thscode.endswith('.SH') else '0'}.{code}",
        "provider_name": str(data.get("f58")),
        "provider_timestamp_field": "f86",
        "volume_raw": data.get("f47"),
        "volume_unit_raw": "hand",
        "volume_unit": "share",
        "amount_raw": data.get("f48"),
        "amount_unit": "CNY",
    }
    ok, reason = validate_market_row(candidate, code, expected_name=candidate.get("provider_name"), market_date=now_dt.date().isoformat(), now=now_dt.astimezone(UTC), runtime_policy=POLICY)
    if not ok:
        raise RuntimeError(f"{code} quality guard: {reason}")
    return candidate


def fetch_etf_market_snapshot(cli: str, run_dir: Path, code: str, thscode: str, market_phase: str) -> dict:
    """Use the direct market endpoint when the fund endpoint does not cover an ETF."""
    obj = run_json(cli, ["market", "snapshot", "--thscodes", thscode], run_dir / f"ETF_{code}_market.json")
    items = obj.get("data", {}).get("item") or []
    exact = [item for item in items if str(item.get("thscode") or "") == thscode]
    if len(exact) != 1:
        raise RuntimeError(f"expected one direct market row for {code}, got {len(exact)}")
    received = now_shanghai()
    candidate = row(
        "ETF", code, thscode, exact[0], received.isoformat(timespec="seconds"),
        obj.get("data", {}).get("timestamp"), market_phase,
        provider="hithink-finance-market", provider_primary="hithink-finance",
        fallback_used=True,
        fallback_reason="fund snapshot unsupported; verified direct Hithink market snapshot",
    )
    ok, reason = validate_market_row(candidate, code, market_date=received.date().isoformat(), now=received.astimezone(UTC), runtime_policy=POLICY)
    if not ok:
        raise RuntimeError(f"{code} direct market quality guard: {reason}")
    return candidate


def fetch_etf_with_fallback(cli: str, run_dir: Path, code: str, thscode: str) -> dict:
    tencent_error = None
    try:
        return fetch_tencent_a_share("ETF", code, thscode, a_share_market_phase(now_shanghai()))
    except Exception as error:
        tencent_error = error

    hithink_fund_error = RuntimeError("hithink-finance fund endpoint not attempted")
    if not PRIMARY_RUN_BYPASS.is_set():
        try:
            return fetch_etf(cli, run_dir, code, thscode)
        except Exception as error:
            hithink_fund_error = error
    else:
        hithink_fund_error = RuntimeError("hithink-finance fund endpoint bypassed after a transient failure earlier in this run")

    # The market snapshot endpoint is an independently validated direct endpoint
    # within the same Hithink provider family. A fund-endpoint coverage failure
    # must not prevent this object-level direct fallback from being attempted.
    hithink_market_error = RuntimeError("hithink-finance market endpoint not attempted")
    try:
        return fetch_etf_market_snapshot(
            cli, run_dir, code, thscode, a_share_market_phase(now_shanghai())
        )
    except Exception as error:
        hithink_market_error = error

    if code in EASTMONEY_FALLBACK_ETFS:
        try:
            return fetch_eastmoney_etf(code, thscode, a_share_market_phase(now_shanghai()))
        except Exception as fallback_error:
            raise RuntimeError(
                f"Tencent primary failed: {tencent_error}; "
                f"Hithink fund fallback failed: {hithink_fund_error}; "
                f"Hithink market fallback failed: {hithink_market_error}; "
                f"Eastmoney fallback failed: {fallback_error}"
            ) from fallback_error

    raise RuntimeError(
        f"Tencent primary failed: {tencent_error}; "
        f"Hithink fund fallback failed: {hithink_fund_error}; "
        f"Hithink market fallback failed: {hithink_market_error}"
    )

def fetch_etf(cli: str, run_dir: Path, code: str, thscode: str) -> dict:
    obj = run_json(cli, ["fund", "snapshot", "--thscode", thscode], run_dir / f"ETF_{code}.json")
    items = obj.get("data", {}).get("item") or []
    if len(items) != 1:
        raise RuntimeError(f"expected one ETF row for {code}, got {len(items)}")
    received = now_shanghai()
    candidate = row("ETF", code, thscode, items[0], received.isoformat(timespec="seconds"), obj.get("data", {}).get("timestamp"), a_share_market_phase(received))
    ok, reason = validate_market_row(candidate, code, market_date=received.date().isoformat(), now=received.astimezone(UTC), runtime_policy=POLICY)
    if not ok:
        raise RuntimeError(f"{code} quality guard: {reason}")
    return candidate


def failed_etf_row(code: str, thscode: str, error: str, market_phase: str) -> dict:
    captured = now_shanghai().isoformat(timespec="seconds")
    return {
        "asset_class": "ETF",
        "symbol": code,
        "thscode": thscode,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "prev_close": None,
        "change_pct": None,
        "volume": None,
        "amount": None,
        "provider": "hithink-finance",
        "provider_primary": "hithink-finance",
        "provider_used": "hithink-finance",
        "fallback_used": False,
        "fallback_reason": "",
        "provider_timestamp_ms": None,
        "as_of_beijing": "",
        "captured_at": captured,
        "captured_at_beijing": captured,
        "timezone": "Asia/Shanghai",
        "market_phase": market_phase,
        "quality_status": "FAILED",
        "error": error[-500:],
        "failure_class": classify_provider_failure(error),
        "semantic_note": "该对象provider请求失败；未使用旧行情、代理或伪造OHLC补齐。"
    }


def is_newer_than_current(captured_dt: datetime) -> bool:
    current = read_current(ROOT)
    text = current.get("captured_at", "")
    if not text:
        return True
    try:
        previous = datetime.fromisoformat(text)
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=SHANGHAI)
    return captured_dt > previous.astimezone(SHANGHAI)



def retry_failed_snapshot() -> int:
    current = read_current(ROOT)
    latest_snapshot = str(current.get("latest_snapshot") or "")
    snapshot_path = ROOT / latest_snapshot
    if not latest_snapshot or not snapshot_path.exists():
        raise RuntimeError("cannot quick-retry without a current snapshot")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    failed_rows = [
        row for row in (snapshot.get("rows") or [])
        if row.get("asset_class") == "ETF" and str(row.get("quality_status") or "").upper() in {"FAILED", "FAIL"}
    ]
    if not failed_rows:
        print(json.dumps({"ok": True, "quick_retry": False, "reason": "no_failed_etf_objects"}, ensure_ascii=False))
        return 0
    started = time.monotonic()
    now_dt = now_shanghai()
    market_phase = a_share_market_phase(now_dt)
    cli = cli_path()
    run_dir = ROOT / "data" / "market" / "raw" / "hithink" / now_dt.date().isoformat() / f"{now_dt:%H%M%S}_quick_retry"
    replacements = {}
    with ThreadPoolExecutor(max_workers=min(max(1, MAX_WORKERS), len(failed_rows))) as pool:
        futures = {pool.submit(fetch_etf_with_fallback, cli, run_dir, str(row.get("symbol")), str(row.get("thscode"))): row for row in failed_rows}
        for future in as_completed(futures):
            original = futures[future]
            code = str(original.get("symbol") or "")
            thscode = str(original.get("thscode") or "")
            try:
                replacements[code] = future.result()
            except Exception as error:
                replacements[code] = failed_etf_row(code, thscode, str(error), market_phase)
    rows = [replacements.get(str(row.get("symbol")), row) for row in (snapshot.get("rows") or [])]
    rows.sort(key=lambda row: (row.get("asset_class", ""), row.get("symbol", "")))
    quality = "PASS" if all(row.get("quality_status") == "PASS" for row in rows) else "DEGRADED"
    finished = now_shanghai()
    captured = finished.isoformat(timespec="seconds")
    elapsed = round(time.monotonic() - started, 3)
    snapshot["rows"] = rows
    snapshot["quality_status"] = quality
    snapshot["captured_at"] = captured
    snapshot["captured_at_beijing"] = captured
    snapshot.setdefault("runtime", {})["quick_retry_objects"] = sorted(replacements)
    snapshot["runtime"]["quick_retry_seconds"] = elapsed
    temp = snapshot_path.with_name(f".{snapshot_path.name}.quick_retry.tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(snapshot_path)
    update_current(root=ROOT, market_date=str(snapshot.get("market_date") or current.get("market_date") or finished.date().isoformat()), node=str(snapshot.get("node") or current.get("latest_valid_node") or "live"), captured_at=captured, latest_snapshot=latest_snapshot, snapshot_commit=os.environ.get("GITHUB_SHA", ""), node_status="READY" if quality == "PASS" else "DEGRADED", data_freshness={**(current.get("data_freshness") or {}), "status": quality, "market_phase": snapshot.get("market_phase") or market_phase, "captured_at": captured, "captured_at_beijing": captured, "quick_retry_objects": sorted(replacements), "quick_retry_seconds": elapsed})
    write_runtime_health({"status": "PASS" if quality == "PASS" else "DEGRADED", "quality_status": quality, "market_date": snapshot.get("market_date", ""), "node": snapshot.get("node", "live"), "market_phase": snapshot.get("market_phase") or market_phase, "captured_at": captured, "captured_at_beijing": captured, "acquisition_seconds": elapsed, "count": len(rows), "latest_snapshot": latest_snapshot, "quick_retry_objects": sorted(replacements), "quick_retry_seconds": elapsed})
    print(json.dumps({"ok": True, "quick_retry": True, "objects": sorted(replacements), "quality_status": quality, "captured_at_beijing": captured, "acquisition_seconds": elapsed}, ensure_ascii=False))
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default="manual", choices=sorted(NODES))
    parser.add_argument("--probe-only", action="store_true", help="Fetch and validate real data without creating a market node")
    parser.add_argument("--retry-failed", action="store_true", help="Retry only failed ETF rows in the latest published snapshot")
    args = parser.parse_args()
    PRIMARY_RUN_BYPASS.clear()
    if args.retry_failed:
        return retry_failed_snapshot()

    run_started_monotonic = time.monotonic()
    run_started_at = now_utc_text()
    run_started_dt = now_shanghai()
    capture_started_at = run_started_dt.isoformat(timespec="seconds")
    market_date = run_started_dt.date().isoformat()
    market_phase = a_share_market_phase(run_started_dt)

    if args.node == "scheduled" and not args.probe_only and not in_a_share_capture_window(run_started_dt):
        write_runtime_health({"status": "SKIPPED", "reason": "outside_a_share_capture_window", "market_date": market_date, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "market_phase": market_phase})
        print(json.dumps({"ok": True, "skipped": True, "reason": "outside_a_share_capture_window", "market_date": market_date, "capture_started_at_beijing": capture_started_at}, ensure_ascii=False))
        return 0

    node = resolve_scheduled_node(run_started_dt) if args.node == "scheduled" else args.node
    # Query-time/manual callers use a generic live label. During 09:15-09:29,
    # normalize that label to the formal opening-auction node so CURRENT.node,
    # snapshot.node and market_phase cannot disagree semantically.
    if market_phase == "OPENING_CALL_AUCTION" and node in {"live", "manual"}:
        node = "auction"
    planned_time = PLANNED_TIMES.get(node, "")

    if args.node == "scheduled" and node == "close" and close_already_recorded(market_date):
        write_runtime_health({"status": "SKIPPED", "reason": "close_already_recorded", "market_date": market_date, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "market_phase": market_phase})
        print(json.dumps({"ok": True, "skipped": True, "reason": "close_already_recorded", "market_date": market_date}, ensure_ascii=False))
        return 0

    if run_started_dt.weekday() >= 5 and not args.probe_only:
        write_runtime_health({"status": "SKIPPED", "reason": "non_trading_weekend", "market_date": market_date, "run_started_at": run_started_at})
        print(json.dumps({"ok": True, "skipped": True, "reason": "non_trading_weekend", "market_date": market_date}, ensure_ascii=False))
        return 0

    cli = cli_path()
    run_dir = ROOT / "data" / "market" / "raw" / "hithink" / market_date / f"{run_started_dt:%H%M%S}"
    snapshot_dir = ROOT / "data" / "market" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS)) as pool:
        futures = {pool.submit(fetch_etf_with_fallback, cli, run_dir, code, thscode): (code, thscode) for code, thscode in ETF}
        for future in as_completed(futures):
            code, thscode = futures[future]
            try:
                rows.append(future.result())
            except Exception as error:  # noqa: BLE001 - preserve object-level provider failure
                failed = failed_etf_row(code, thscode, str(error), a_share_market_phase(now_shanghai()))
                rows.append(failed)
                print(json.dumps({"object_failure": failed}, ensure_ascii=False))

    for code, thscode in INDEX:
        received = now_shanghai()
        try:
            rows.append(fetch_tencent_a_share("A_SHARE_INDEX", code, thscode, a_share_market_phase(received)))
            continue
        except Exception as tencent_error:
            primary_error = tencent_error
        try:
            if PRIMARY_RUN_BYPASS.is_set():
                raise RuntimeError("hithink-finance bypassed after a transient failure earlier in this run")
            obj = run_json(cli, ["index", "snapshot", "--thscodes", thscode], run_dir / f"INDEX_{code}.json")
            returned = {x.get("thscode"): x for x in (obj.get("data", {}).get("item") or [])}
            if thscode not in returned:
                raise RuntimeError(f"missing index row for {thscode}")
            rows.append(row("A_SHARE_INDEX", code, thscode, returned[thscode], received.isoformat(timespec="seconds"), obj.get("data", {}).get("timestamp"), a_share_market_phase(received)))
            continue
        except Exception as hithink_error:
            primary_error = RuntimeError(f"Tencent={primary_error}; Hithink={hithink_error}")
        try:
            rows.append(fetch_eastmoney_index(code, thscode, a_share_market_phase(received)))
        except Exception as fallback_error:
            rows.append(failed_index_row(code, thscode, f"primary={primary_error}; fallback={fallback_error}", a_share_market_phase(received)))

    rows.sort(key=lambda x: (x["asset_class"], x["symbol"]))
    overall_quality = "PASS" if all(item.get("quality_status") == "PASS" for item in rows) else "DEGRADED"
    providers_used = sorted({str(item.get("provider_used") or item.get("provider") or "") for item in rows if item.get("provider_used") or item.get("provider")})
    fallback_rows = [item for item in rows if item.get("fallback_used") is True]
    failed_rows = [item for item in rows if str(item.get("quality_status") or "").upper() in {"FAILED", "FAIL"}]
    coverage = {
        "planned_count": len(rows),
        "primary_success_count": sum(1 for item in rows if item.get("fallback_used") is not True and str(item.get("quality_status") or "").upper() not in {"FAILED", "FAIL"}),
        "fallback_success_count": len(fallback_rows),
        "failed_count": len(failed_rows),
        "failed_objects": [str(item.get("symbol") or "") for item in failed_rows],
        "single_source_risk": ["000001"] if any(str(item.get("symbol")) == "000001" and item.get("quality_status") != "PASS" for item in rows) else [],
        "auto_healed": bool(fallback_rows),
        "status": "AUTO_HEALED" if fallback_rows and not failed_rows else ("DEGRADED" if failed_rows else "PASS"),
    }
    snapshot_provider = providers_used[0] if len(providers_used) == 1 else "mixed"
    runtime_status = "AUTO_HEALED" if coverage["auto_healed"] and not failed_rows else ("DEGRADED" if failed_rows else "PASS")
    acquisition_seconds = round(time.monotonic() - run_started_monotonic, 3)
    captured_dt = now_shanghai()
    captured = captured_dt.isoformat(timespec="seconds")
    market_phase = a_share_market_phase(captured_dt)

    if args.probe_only:
        write_runtime_health({"status": "PROBE_PASS", "market_date": market_date, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "captured_at": captured, "captured_at_beijing": captured, "market_phase": market_phase, "acquisition_seconds": acquisition_seconds, "count": len(rows)})
        print(json.dumps({"ok": True, "probe_only": True, "count": len(rows), "market_date": market_date, "quality_status": overall_quality, "node_written": False, "market_phase": market_phase, "captured_at_beijing": captured, "acquisition_seconds": acquisition_seconds, "etf_universe_count": len(ETF)}, ensure_ascii=False))
        return 0

    if not is_newer_than_current(captured_dt):
        write_runtime_health({"status": "SUPERSEDED", "reason": "newer_current_already_exists", "market_date": market_date, "run_started_at": run_started_at, "captured_at": captured, "captured_at_beijing": captured, "market_phase": market_phase, "acquisition_seconds": acquisition_seconds})
        print(json.dumps({"ok": True, "skipped": True, "reason": "newer_current_already_exists", "captured_at_beijing": captured}, ensure_ascii=False))
        return 0

    snapshot = {
        "market_date": market_date, "node": node, "planned_time": planned_time,
        "market_phase": market_phase,
        "actual_run_time": captured, "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "capture_started_at_beijing": capture_started_at,
        "captured_at": captured, "captured_at_beijing": captured, "timezone": "Asia/Shanghai", "provider": snapshot_provider,
        "quality_status": overall_quality, "count": len(rows), "etf_universe_count": len(ETF),
        "semantic_scope": "集合竞价脉冲只按集合竞价信息解释；连续竞价脉冲才按盘中成交语义解释。",
        "runtime": {"acquisition_seconds": acquisition_seconds, "target_cadence_seconds": POLICY["target_cadence_seconds"], "provider_timeout_seconds": TIMEOUT_SECONDS, "provider_retry_limit": RETRY_LIMIT, "provider_max_workers": MAX_WORKERS, "close_grace_seconds": CLOSE_GRACE_SECONDS, "analysis_coverage": coverage},
        "rows": rows,
    }
    name = f"{captured_dt:%Y-%m-%d_%H%M%S}.json"
    temp = snapshot_dir / f".{name}.tmp"
    target = snapshot_dir / name
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)

    capture_mode = "OPENING_AUCTION_PULSE" if market_phase == "OPENING_CALL_AUCTION" else ("CLOSE" if node == "close" else "INTRADAY_PULSE")
    update_current(root=ROOT, market_date=market_date, node=node, captured_at=captured, latest_snapshot=str(target.relative_to(ROOT)).replace("\\", "/"), snapshot_commit=os.environ.get("GITHUB_SHA", ""), node_status=("READY" if overall_quality == "PASS" else "DEGRADED"), data_freshness={"status": overall_quality, "provider": snapshot_provider, "count": len(rows), "etf_universe_count": len(ETF), "capture_mode": capture_mode, "market_phase": market_phase, "captured_at": captured, "captured_at_beijing": captured, "target_cadence_seconds": POLICY["target_cadence_seconds"], "fresh_max_age_seconds": POLICY["fresh_max_age_seconds"], "degraded_max_age_seconds": POLICY["degraded_max_age_seconds"], "acquisition_seconds": acquisition_seconds})
    write_runtime_health({"status": runtime_status, "quality_status": overall_quality, "market_date": market_date, "node": node, "market_phase": market_phase, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "captured_at": captured, "captured_at_beijing": captured, "acquisition_seconds": acquisition_seconds, "count": len(rows), "latest_snapshot": str(target.relative_to(ROOT)).replace("\\", "/"), "analysis_coverage": coverage})
    print(json.dumps({"ok": True, "snapshot": str(target), "count": len(rows), "market_date": market_date, "node": node, "market_phase": market_phase, "captured_at_beijing": captured, "acquisition_seconds": acquisition_seconds, "etf_universe_count": len(ETF)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        try:
            write_runtime_health({"status": "FAILED", "reason": "collection_error", "error": str(exc)[-1000:], "failed_at": now_utc_text(), "preserve_previous_current": True})
        except Exception:
            pass
        print(f"cloud runner failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
