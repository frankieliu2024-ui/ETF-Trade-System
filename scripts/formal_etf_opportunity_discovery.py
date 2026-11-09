from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from hyperliquid_contribution import build_contribution

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
SPOT_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
HITHINK_ETF_MASTER_URL = "https://fund.10jqka.com.cn/data/Net/info/ETF_rate_desc_0_0_1_9999_0_0_0_jsonp_g.html"
HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
ETF_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827"
FIELDS = "f2,f3,f6,f7,f8,f10,f12,f13,f14,f15,f16,f17,f18,f24,f25,f26,f124"
MIN_AMOUNT = 10_000_000.0
MAX_OBSERVATION_INPUTS_PER_FAMILY = 4
MAX_OBSERVATION_CANDIDATES = 12
MAX_HISTORY_SUCCESS_BUDGET = 12
MAX_HISTORY_ATTEMPT_MULTIPLIER = 3
MAX_HISTORY_FETCH_WORKERS = 2
MAX_TENCENT_HYDRATION_WORKERS = 6
MIN_HISTORY = 65
RELATIVE_DIVERGENCE_PCT = 1.5
SHORT_HISTORY_MIN = 20


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _request_json(url: str, params: dict[str, Any], timeout: int = 12) -> dict[str, Any]:
    req = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _spot_row(raw: dict[str, Any]) -> dict[str, Any]:
    code = str(raw.get("f12") or "")
    return {
        "code": code,
        "name": str(raw.get("f14") or code),
        "market_id": int(_num(raw.get("f13")) or (1 if code.startswith("5") else 0)),
        "price": _num(raw.get("f2")),
        "change_pct": _num(raw.get("f3")),
        "amount": _num(raw.get("f6")),
        "amplitude_pct": _num(raw.get("f7")),
        "turnover_pct": _num(raw.get("f8")),
        "volume_ratio": _num(raw.get("f10")),
        "high": _num(raw.get("f15")),
        "low": _num(raw.get("f16")),
        "open": _num(raw.get("f17")),
        "prev_close": _num(raw.get("f18")),
        "return_60d_pct": _num(raw.get("f24")),
        "return_ytd_pct": _num(raw.get("f25")),
        "listing_date": str(raw.get("f26") or ""),
        "provider_timestamp": raw.get("f124"),
    }


def fetch_broad_etf_spot() -> list[dict[str, Any]]:
    def fetch_page(page: int) -> tuple[list[dict[str, Any]], int]:
        payload = _request_json(SPOT_URL, {
            "pn": page, "pz": 100, "po": 0, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2, "invt": 2,
            "fid": "f12", "fs": ETF_FS, "fields": FIELDS,
        })
        data = payload.get("data") or {}
        diff = data.get("diff") or []
        return [_spot_row(x) for x in diff if isinstance(x, dict)], int(data.get("total") or 0)

    first_rows, total = fetch_page(1)
    if not first_rows:
        return []
    page_count = max(1, min(50, (total + 99) // 100)) if total else 1
    if page_count == 1:
        return first_rows

    # Pages are independent snapshots of the same broad cross-section. Fetch
    # the remaining pages concurrently, but consume them in page order so
    # provider timing never becomes a hidden ranking signal.
    page_rows: dict[int, list[dict[str, Any]]] = {1: first_rows}
    with ThreadPoolExecutor(max_workers=min(6, page_count - 1), thread_name_prefix="eastmoney-etf-page") as executor:
        futures = {executor.submit(fetch_page, page): page for page in range(2, page_count + 1)}
        for future in as_completed(futures):
            page = futures[future]
            rows, observed_total = future.result()
            if observed_total and total and observed_total != total:
                raise RuntimeError(f"Eastmoney ETF pagination total changed: first={total} page{page}={observed_total}")
            page_rows[page] = rows
    return [row for page in range(1, page_count + 1) for row in page_rows.get(page, [])]


def _request_json_headers(url: str, params: dict[str, Any], headers: dict[str, str], timeout: int = 12) -> Any:
    req = Request(f"{url}?{urlencode(params)}", headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_hithink_etf_master() -> list[dict[str, Any]]:
    """Enumerate Hithink ETF identities for the secondary resilience path only."""
    req = Request(
        HITHINK_ETF_MASTER_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://fund.10jqka.com.cn/datacenter/jz/kfs/etf/",
            "Accept": "application/json,text/javascript,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=15) as resp:
        payload_text = resp.read().decode("utf-8", errors="replace").strip()
    if payload_text.startswith("g(") and payload_text.endswith(")"):
        payload_text = payload_text[2:-1]
    elif payload_text.startswith("(") and payload_text.endswith(")"):
        payload_text = payload_text[1:-1]
    payload = json.loads(payload_text)
    raw_rows = ((payload.get("data") or {}).get("data") or {})
    if not isinstance(raw_rows, dict) or not raw_rows:
        raise RuntimeError("Hithink ETF master returned empty or unexpected data")
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for raw in raw_rows.values():
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip()
        name = str(raw.get("name") or code).strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        market_id = 1 if code.startswith(("5", "6")) else 0
        result[(market_id, code)] = {
            "code": code,
            "name": name or code,
            "market_id": market_id,
            "exchange": "SSE" if market_id == 1 else "SZSE",
            "identity_source": "HITHINK_ETF_CATEGORY_ENUMERATION",
        }
    if not result:
        raise RuntimeError("Hithink ETF master parsed zero six-digit ETF identities")
    return [result[key] for key in sorted(result)]


def _tencent_spot_row(identity: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": identity["code"], "name": str(quote.get("name") or identity.get("name") or identity["code"]),
        "market_id": identity["market_id"], "price": _num(quote.get("last_price")),
        "change_pct": _num(quote.get("price_change_ratio_pct")), "amount": _num(quote.get("turnover")),
        "amplitude_pct": None, "turnover_pct": None, "volume_ratio": None,
        "high": _num(quote.get("high_price")), "low": _num(quote.get("low_price")),
        "open": _num(quote.get("open_price")), "prev_close": _num(quote.get("prev_price")),
        "return_60d_pct": None, "return_ytd_pct": None, "listing_date": "",
        "provider_timestamp": quote.get("provider_timestamp_ms"),
        "broad_quote_source": "TENCENT_QQ", "identity_source": identity.get("identity_source"),
    }


def _hydrate_tencent_identities(identities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Hydrate only the identities that need independent current-market facts."""
    try:
        from scripts.tencent_quote import fetch_tencent_quotes
    except ModuleNotFoundError:
        from tencent_quote import fetch_tencent_quotes

    batches = [
        (offset, identities[offset:offset + 60])
        for offset in range(0, len(identities), 60)
    ]

    def hydrate_batch(offset: int, batch: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], dict[str, Any] | None]:
        symbols = [f'{x["code"]}.{"SH" if x["market_id"] == 1 else "SZ"}' for x in batch]
        try:
            quotes = fetch_tencent_quotes(symbols, timeout=10)
        except Exception as exc:
            return offset, [], {"offset": offset, "count": len(batch), "error": str(exc)[-240:]}
        batch_rows = []
        for identity, symbol in zip(batch, symbols):
            quote = quotes.get(symbol.upper())
            if quote:
                batch_rows.append(_tencent_spot_row(identity, quote))
        return offset, batch_rows, None

    completed: dict[int, tuple[list[dict[str, Any]], dict[str, Any] | None]] = {}
    if batches:
        with ThreadPoolExecutor(
            max_workers=min(MAX_TENCENT_HYDRATION_WORKERS, len(batches)),
            thread_name_prefix="tencent-etf-hydration",
        ) as executor:
            futures = {
                executor.submit(hydrate_batch, offset, batch): offset
                for offset, batch in batches
            }
            for future in as_completed(futures):
                offset, batch_rows, failure = future.result()
                completed[offset] = (batch_rows, failure)

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for offset, _batch in batches:
        batch_rows, failure = completed.get(offset, ([], {"offset": offset, "count": len(_batch), "error": "missing hydration result"}))
        rows.extend(batch_rows)
        if failure:
            failures.append(failure)
    return rows, {
        "requested_identity_count": len(identities),
        "tencent_quote_count": len(rows),
        "tencent_failed_batch_count": len(failures),
        "tencent_failures": failures,
        "tencent_hydration_batch_count": len(batches),
        "tencent_hydration_workers": min(MAX_TENCENT_HYDRATION_WORKERS, len(batches)) if batches else 0,
    }


def fetch_reconciled_broad_etf_spot(market_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Eastmoney-first broad ETF universe with Hithink/Tencent fallback only.

    When Eastmoney returns a valid broad cross-section, it is the complete
    primary universe for this run. Hithink is not unioned into that universe.
    Hithink identities are consulted only after Eastmoney fails, then hydrated
    through Tencent as an explicitly degraded secondary path.
    """
    started = time.monotonic()
    east_error = None
    try:
        east_rows = fetch_broad_etf_spot()
    except Exception as exc:
        east_rows = []
        east_error = str(exc)[-500:]
    east_elapsed = round(time.monotonic() - started, 3)

    if east_rows:
        return east_rows, {
            "universe_mode": "EASTMONEY_PRIMARY",
            "primary_source": "EASTMONEY_PUSH2_ETF_CROSS_SECTION",
            "eastmoney_count": len(east_rows),
            "hithink_identity_count": 0,
            "eastmoney_error": None,
            "hithink_error": None,
            "fallback_used": False,
            "fallback_source": None,
            "reconciled_count": len(east_rows),
            "source_elapsed_seconds": {"eastmoney": east_elapsed},
            "source_join_elapsed_seconds": east_elapsed,
            "supplemental_hydration_elapsed_seconds": 0.0,
        }

    hithink_started = time.monotonic()
    try:
        hithink_master = fetch_hithink_etf_master()
    except Exception as exc:
        hithink_master = []
        hithink_error = str(exc)[-500:]
    else:
        hithink_error = None
    hithink_elapsed = round(time.monotonic() - hithink_started, 3)

    if not hithink_master:
        raise RuntimeError(
            f"primary and secondary ETF universe sources unavailable; "
            f"eastmoney={east_error}; hithink={hithink_error}"
        )

    hydration_started = time.monotonic()
    hydrated, hydration_meta = _hydrate_tencent_identities(hithink_master)
    hydration_elapsed = round(time.monotonic() - hydration_started, 3)
    if not hydrated:
        raise RuntimeError(
            f"Eastmoney unavailable and Hithink/Tencent fallback produced no ETF quotes; "
            f"eastmoney={east_error}; hithink_quote_failures={hydration_meta.get('tencent_failures')}"
        )
    return hydrated, {
        **hydration_meta,
        "universe_mode": "HITHINK_TENCENT_SECONDARY_FALLBACK",
        "primary_source": "EASTMONEY_PUSH2_ETF_CROSS_SECTION",
        "eastmoney_count": 0,
        "hithink_identity_count": len(hithink_master),
        "eastmoney_error": east_error,
        "hithink_error": hithink_error,
        "fallback_used": True,
        "fallback_source": "HITHINK_ETF_MASTER_PLUS_TENCENT",
        "reconciled_count": len(hydrated),
        "source_elapsed_seconds": {"eastmoney": east_elapsed, "hithink": hithink_elapsed},
        "source_join_elapsed_seconds": east_elapsed + hithink_elapsed,
        "supplemental_hydration_elapsed_seconds": hydration_elapsed,
    }


def _secid(code: str, market_id: int | None = None) -> str:
    market = market_id if market_id in {0, 1} else (1 if str(code).startswith("5") else 0)
    return f"{market}.{code}"


def fetch_eastmoney_daily_history(code: str, market_id: int, end_date: str, limit: int = 90) -> list[dict[str, Any]]:
    payload = _request_json(HISTORY_URL, {
        "secid": _secid(code, market_id),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": 0, "end": end_date.replace("-", ""), "lmt": limit,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
    })
    klines = ((payload.get("data") or {}).get("klines") or [])
    rows = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        rows.append({
            "date": parts[0], "open": _num(parts[1]), "close": _num(parts[2]),
            "high": _num(parts[3]), "low": _num(parts[4]),
            "volume": _num(parts[5]), "amount": _num(parts[6]), "_provider": "eastmoney_push2his",
        })
    return [x for x in rows if x["date"] and x["close"] is not None]


def fetch_hithink_daily_history_bounded(code: str, market_id: int, end_date: str, limit: int = 90) -> list[dict[str, Any]]:
    """Fetch bounded raw ETF daily bars from the existing Hithink Finance API credential."""
    api_key = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("HITHINK_FINANCE_API_KEY is not configured")
    suffix = "SH" if market_id == 1 else "SZ"
    end = datetime.fromisoformat(end_date).replace(tzinfo=BEIJING) + timedelta(days=1) - timedelta(milliseconds=1)
    start = end - timedelta(days=180)
    params = {
        "thscode": f"{code}.{suffix}", "interval": "1d",
        "start": int(start.timestamp() * 1000), "end": int(end.timestamp() * 1000),
    }
    req = Request(
        f"https://fuyao.aicubes.cn/api/fund/market/historical?{urlencode(params)}",
        headers={"X-api-key": api_key, "Accept": "application/json", "User-Agent": "ETF-Swing-System/2.2.14"},
    )
    with urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"hithink history business error: {payload.get('code')} {payload.get('message')}")
    data = payload.get("data") or {}
    if str(data.get("thscode") or "").upper() != f"{code}.{suffix}" or data.get("interval") != "1d":
        raise RuntimeError("unexpected hithink history identity")
    rows = []
    for row in data.get("item") or []:
        date_ms = row.get("date_ms")
        if date_ms in (None, ""):
            continue
        day = datetime.fromtimestamp(int(date_ms) / 1000, timezone.utc).astimezone(BEIJING).date().isoformat()
        rows.append({
            "date": day, "open": _num(row.get("open_price")), "close": _num(row.get("close_price")),
            "high": _num(row.get("high_price")), "low": _num(row.get("low_price")),
            "volume": _num(row.get("volume")), "amount": _num(row.get("turnover")), "_provider": "hithink_finance_history",
        })
    rows = [x for x in rows if x["date"] and x["close"] is not None and x["date"] <= end_date]
    rows.sort(key=lambda x: x["date"])
    return rows[-limit:]


def fetch_tencent_daily_history_bounded(code: str, market_id: int, end_date: str, limit: int = 90) -> list[dict[str, Any]]:
    try:
        from scripts.tencent_quote import fetch_tencent_daily_history
    except ModuleNotFoundError:
        from tencent_quote import fetch_tencent_daily_history

    suffix = "SH" if market_id == 1 else "SZ"
    end = datetime.fromisoformat(end_date).date()
    start = end - timedelta(days=180)
    rows, _meta = fetch_tencent_daily_history(
        f"{code}.{suffix}", start, end, adjustment="none", timeout=10, page_size=180, window_days=181
    )
    normalized = []
    for row in rows[-limit:]:
        normalized.append({
            "date": str(row.get("date") or ""), "open": _num(row.get("open")), "close": _num(row.get("close")),
            "high": _num(row.get("high")), "low": _num(row.get("low")), "volume": _num(row.get("volume")),
            "amount": _num(row.get("amount")), "_provider": "tencent_qq_history",
        })
    return [x for x in normalized if x["date"] and x["close"] is not None]


def fetch_daily_history(code: str, market_id: int, end_date: str, limit: int = 90) -> list[dict[str, Any]]:
    """Independent-provider bounded repair for Discovery completed daily bars."""
    errors = []
    try:
        rows = fetch_hithink_daily_history_bounded(code, market_id, end_date, limit)
        if rows:
            return rows
        errors.append("hithink_finance_history:empty")
    except Exception as exc:
        errors.append(f"hithink_finance_history:{type(exc).__name__}:{exc}")
    try:
        rows = fetch_tencent_daily_history_bounded(code, market_id, end_date, limit)
        if rows:
            return rows
        errors.append("tencent_qq_history:empty")
    except Exception as exc:
        errors.append(f"tencent_qq_history:{type(exc).__name__}:{exc}")
    try:
        rows = fetch_eastmoney_daily_history(code, market_id, end_date, limit)
        if rows:
            return rows
        errors.append("eastmoney_push2his:empty")
    except Exception as exc:
        errors.append(f"eastmoney_push2his:{type(exc).__name__}:{exc}")
    raise RuntimeError("historical provider chain exhausted; " + " | ".join(errors))


def required_completed_history_date(root: Path, execution_date: str, *, include_execution_date: bool = False) -> str:
    """Latest fully completed A-share session available at the execution point."""
    calendar_path = root / "config/market/a_share_trading_calendar_2026.json"
    closed: set[str] = set()
    if calendar_path.exists():
        try:
            closed = set(json.loads(calendar_path.read_text(encoding="utf-8")).get("closed_dates") or [])
        except Exception:
            closed = set()
    day = datetime.fromisoformat(execution_date).date()
    if not include_execution_date:
        day -= timedelta(days=1)
    for _ in range(370):
        iso = day.isoformat()
        if day.weekday() < 5 and iso not in closed:
            return iso
        day -= timedelta(days=1)
    raise RuntimeError(f"cannot resolve required completed-history PIT for {execution_date}")


def _history_is_current_for_required_date(rows: list[dict[str, Any]] | None, required_history_end_date: str) -> bool:
    if not rows:
        return False
    usable_dates = {str(row.get("date") or "") for row in rows if row.get("date")}
    return required_history_end_date in usable_dates


def _history_snapshot_path(root: Path, market_date: str) -> Path:
    return root / "data/market/discovery_history" / f"{market_date}.json"


def load_discovery_history_snapshot(root: Path, market_date: str) -> dict[str, list[dict[str, Any]]]:
    path = _history_snapshot_path(root, market_date)
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if str(obj.get("market_date") or "") != market_date:
        return {}
    histories = obj.get("histories") or {}
    return {str(code): rows for code, rows in histories.items() if isinstance(rows, list) and len(rows) >= MIN_HISTORY}


def persist_discovery_history_snapshot(root: Path, market_date: str, histories: dict[str, list[dict[str, Any]]]) -> None:
    path = _history_snapshot_path(root, market_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "market_date": market_date,
        "semantics": "NODE_LOCAL_REUSABLE_COMPLETED_HISTORY_INPUT_NOT_MANAGEMENT_STATE_NOT_TRADE_AUTHORITY",
        "histories": histories,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def load_validated_history(root: Path, code: str, required_history_end_date: str, limit: int = 90) -> list[dict[str, Any]] | None:
    result_dir = root / "data/market/on_demand/results"
    best: tuple[str, Path] | None = None
    for path in result_dir.glob(f"*_{code}_*.json"):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not obj.get("ok") or obj.get("asset_type") != "etf" or obj.get("mode") != "history":
            continue
        last_date, dataset = str(obj.get("last_date") or ""), obj.get("dataset")
        if not dataset or not last_date:
            continue
        candidate = root / str(dataset)
        if candidate.exists() and (best is None or last_date > best[0]):
            best = (last_date, candidate)
    if best is None:
        return None
    rows: list[dict[str, Any]] = []
    with best[1].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("date") or "") > required_history_end_date:
                continue
            close = _num(row.get("close"))
            if close is None:
                continue
            rows.append({"date": str(row["date"]), "open": _num(row.get("open")), "high": _num(row.get("high")), "low": _num(row.get("low")), "close": close, "volume": _num(row.get("volume")), "amount": _num(row.get("amount"))})
    return rows[-limit:] if len(rows) >= MIN_HISTORY else None


def _ret(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return None
    return closes[-1] / closes[-sessions - 1] - 1.0


def _max_drawdown(closes: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in closes:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return abs(worst)


def classify_states(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [float(x["close"]) for x in rows if _num(x.get("close")) is not None]
    if len(closes) < MIN_HISTORY:
        return []
    states: list[dict[str, Any]] = []
    returns = {w: _ret(closes, w) for w in (5, 20, 40, 60)}
    positives = [w for w in (20, 40, 60) if returns.get(w) is not None and returns[w] > 0]
    drawdown = _max_drawdown(closes[-61:])
    if len(positives) >= 2 and drawdown <= 0.12:
        states.append({"state": "PERSISTENT_TREND", "evidence": {
            "window_returns": {str(k): round(v, 6) if v is not None else None for k, v in returns.items()},
            "positive_windows": positives, "max_drawdown_60": round(drawdown, 6),
        }})

    short_ret, long_ret = _ret(closes, 10), _ret(closes, 40)
    if short_ret is not None and long_ret is not None:
        gap = short_ret - long_ret * 10 / 40
        if gap >= 0.05:
            states.append({"state": "TREND_CHANGE", "evidence": {
                "direction": "STRENGTHENING", "return_10": round(short_ret, 6),
                "return_40": round(long_ret, 6), "short_vs_long_normalized_gap": round(gap, 6),
            }})

    five_ret, twenty_ret = returns.get(5), returns.get(20)
    if five_ret is not None and twenty_ret is not None:
        short_term_gap = five_ret - twenty_ret * 5 / 20
        if short_term_gap >= 0.05:
            states.append({"state": "SHORT_TERM_CHANGE", "evidence": {
                "direction": "SHORT_TERM_STRENGTHENING",
                "return_5": round(five_ret, 6),
                "return_20": round(twenty_ret, 6),
                "short_vs_medium_normalized_gap": round(short_term_gap, 6),
            }})

    window = closes[-41:]
    if len(window) == 41:
        prior, current = window[:-1], window[-1]
        prior_high, prior_low = max(prior), min(prior)
        if prior_high > 0:
            prior_drawdown = 1.0 - prior_low / prior_high
            recovered = (current - prior_low) / max(prior_high - prior_low, 1e-12)
            if prior_drawdown >= 0.08 and recovered >= 0.65 and current >= prior_high * 0.985:
                states.append({"state": "RECOVERY_BREAKOUT", "evidence": {
                    "prior_drawdown": round(prior_drawdown, 6),
                    "recovery_fraction": round(recovered, 6),
                    "distance_to_prior_high": round(current / prior_high - 1.0, 6),
                }})
    return states


def _state_names(rows: list[dict[str, Any]]) -> set[str]:
    return {str(x.get("state")) for x in classify_states(rows)}


def _broad_return_median(rows: list[dict[str, Any]]) -> float | None:
    values = sorted(x for row in rows if (x := _num(row.get("change_pct"))) is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0



def _apply_external_driver_triggers(rows: list[dict[str, Any]], context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Attach Hyperliquid evidence to existing A-share rows; never creates ETF rows."""
    if not context or str(context.get("quality_status") or "").upper() not in {"PASS", "DEGRADED"}:
        return rows
    mappings = context.get("mapping") or []
    observations = {
        str(item.get("asset", {}).get("name") or ""): item
        for item in (context.get("observations") or [])
        if isinstance(item, dict)
    }
    by_code: dict[str, list[dict[str, Any]]] = {}
    for mapping in mappings:
        asset = str(mapping.get("asset") or "")
        observation = observations.get(asset)
        if not observation:
            continue
        raw = observation.get("context") or {}
        def number(key: str) -> float | None:
            return _num(raw.get(key))
        mark, prev = number("markPx"), number("prevDayPx")
        premium, funding = number("premium"), number("funding")
        if mark is None or prev in (None, 0):
            continue
        direction = "SUPPORTIVE" if mark > prev else ("NEGATIVE" if mark < prev else "NEUTRAL")
        crowding = "ELEVATED" if abs(funding or 0) >= 0.0001 or abs(premium or 0) >= 0.002 else "NORMAL"
        evidence = {
            "asset": asset, "dex": observation.get("dex"), "family": mapping.get("family"),
            "direction": direction, "crowding": crowding, "mark": mark,
            "oracle": number("oraclePx"), "premium": premium, "funding": funding,
            "open_interest": number("openInterest"), "day_notional_volume": number("dayNtlVlm"),
            "provider_timestamp": observation.get("provider_timestamp"),
            "provider_timestamp_status": observation.get("provider_timestamp_status"),
            "as_of_collection_utc": observation.get("collected_at_utc"),
            "decision_effect": (
                "SUPPORT"
                if direction == "SUPPORTIVE" and crowding == "NORMAL"
                else "TIMING_FILTER"
                if direction == "SUPPORTIVE" and crowding == "ELEVATED"
                else "CONTRADICTION_AND_CROWDING"
                if direction == "NEGATIVE" and crowding == "ELEVATED"
                else "CONTRADICTION"
                if direction == "NEGATIVE"
                else "NO_INCREMENTAL_VALUE"
            ),
            "decision_effect_scope": [
                "opportunity_strength",
                "entry_timing",
                "holding_management",
                "capital_competition",
            ],
            "decision_boundary": "对象级外部证据可以改变机会强弱、入场时机、持仓管理和资本竞争判断，但不得独立生成交易权限、Trial、Confirm、金额、卖出份额或订单"
        }
        for code in mapping.get("etf_codes") or []:
            by_code.setdefault(str(code), []).append(evidence)
        mapping["_evidence_template"] = evidence
    enriched = []
    for row in rows:
        copy = dict(row)
        triggers = list(by_code.get(str(row.get("code") or ""), []))
        row_name = str(row.get("name") or "")
        for mapping in mappings:
            markers = [str(x) for x in (mapping.get("name_markers") or []) if str(x)]
            if markers and any(marker in row_name for marker in markers):
                template = mapping.get("_evidence_template")
                if template and template not in triggers:
                    triggers.append(template)
        if triggers:
            copy["_external_driver_triggers"] = triggers
        enriched.append(copy)
    return enriched

def _information_classes(row: dict[str, Any], broad_median: float | None) -> list[str]:
    """Cheap evidence-acquisition classes. They are not trade/capital rankings."""
    if (_num(row.get("amount")) or 0) < MIN_AMOUNT or (_num(row.get("price")) or 0) <= 0:
        return []
    r60 = _num(row.get("return_60d_pct"))
    daily = _num(row.get("change_pct"))
    vr = _num(row.get("volume_ratio"))
    classes: list[str] = []
    if daily is not None and broad_median is not None and daily - broad_median >= RELATIVE_DIVERGENCE_PCT:
        classes.append("NEW_RELATIVE_DIVERGENCE")
    if daily is not None and daily > 0 and (r60 is None or r60 < 15):
        classes.append("STRUCTURAL_CHANGE")
    if r60 is not None and r60 <= 0 and daily is not None and daily > 0 and (vr is None or vr >= 1.0):
        if "STRUCTURAL_CHANGE" not in classes:
            classes.append("STRUCTURAL_CHANGE")
    if r60 is not None and r60 > 0:
        classes.append("PERSISTENT_STRUCTURE")
    if r60 is None and daily is not None and broad_median is not None and daily - broad_median >= RELATIVE_DIVERGENCE_PCT:
        classes.append("SHORT_HISTORY_CURRENT_CHANGE")
    if row.get("_external_driver_triggers"):
        classes.append("EXTERNAL_DRIVER_TRIGGER")
    return classes


def _potential_families(row: dict[str, Any]) -> list[str]:
    """Compatibility view for history-state alignment; scheduling uses information classes."""
    r60 = _num(row.get("return_60d_pct"))
    daily = _num(row.get("change_pct"))
    vr = _num(row.get("volume_ratio"))
    families = []
    if (_num(row.get("amount")) or 0) < MIN_AMOUNT or (_num(row.get("price")) or 0) <= 0:
        return families
    if r60 is not None and r60 > 0:
        families.append("PERSISTENT_TREND")
    if daily is not None and daily > 0 and (r60 is None or r60 < 15):
        families.append("TREND_CHANGE")
    if r60 is not None and r60 <= 0 and daily is not None and daily > 0 and (vr is None or vr >= 1.0):
        families.append("RECOVERY_BREAKOUT")
    return families


def _exposure_key(row: dict[str, Any]) -> str:
    """Cheap clone control: normalize the economic label without a new security master."""
    name = "".join(str(row.get("name") or "").upper().split())
    for marker in ("ETF", "LOF"):
        pos = name.find(marker)
        if pos >= 0:
            return name[:pos + len(marker)]
    return name or str(row.get("code") or "")


def _opportunity_eligible(row: dict[str, Any]) -> bool:
    """Cheap economic gate before scarce history work; never grants trade authority."""
    name = "".join(str(row.get("name") or "").upper().split())
    # Cash-management ETFs are designed to preserve near-cash value rather than
    # express a swing-price hypothesis. This narrow semantic exclusion is
    # intentionally not generalized to bond/fixed-income asset classes.
    cash_management_markers = ("货币ETF", "保证金ETF", "快线ETF", "快钱ETF")
    if any(marker.upper() in name for marker in cash_management_markers):
        return False
    return True


def _bounded_prefilter(rows: list[dict[str, Any]], held_codes: set[str] | None = None, market_date: str = "") -> list[dict[str, Any]]:
    """Allocate scarce validation work by information class, never by return score."""
    held_codes = {str(x) for x in (held_codes or set())}
    class_priority = (
        "EXTERNAL_DRIVER_TRIGGER",
        "NEW_RELATIVE_DIVERGENCE",
        "STRUCTURAL_CHANGE",
        "SHORT_HISTORY_CURRENT_CHANGE",
        "PERSISTENT_STRUCTURE",
    )
    broad_median = _broad_return_median(rows)
    buckets: dict[str, dict[str, dict[str, Any]]] = {x: {} for x in class_priority}
    for row in rows:
        code = str(row.get("code") or "")
        # Managed identities remain in Discovery. Holdings, Observation ETFs
        # and Opportunity ETFs must be re-evaluated alongside new candidates;
        # formal decision compares their roles later.
        if not _opportunity_eligible(row):
            continue
        exposure = _exposure_key(row)
        for info_class in _information_classes(row, broad_median):
            current = buckets[info_class].get(exposure)
            # Conservative clone control: deterministic representative only when
            # the cheap normalized label is exactly identical.
            if current is None or code < str(current.get("code") or ""):
                buckets[info_class][exposure] = row

    ordered: dict[str, list[dict[str, Any]]] = {}
    for info_class in class_priority:
        members = list(buckets[info_class].values())
        members.sort(key=lambda x: hashlib.sha256(
            f"{market_date}|{info_class}|{_exposure_key(x)}".encode("utf-8")
        ).hexdigest())
        ordered[info_class] = members

    queue: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    def append_item(item: dict[str, Any]) -> None:
        code = str(item.get("code") or "")
        if not code or code in seen_codes:
            return
        seen_codes.add(code)
        tagged = dict(item)
        tagged["_discovery_information_classes"] = _information_classes(item, broad_median)
        tagged["_broad_return_median_pct"] = broad_median
        queue.append(tagged)

    # The scarce front of the queue must not be monopolized by one information
    # class merely because that class is earlier in the priority tuple. Give
    # each non-empty class one deterministic opportunity per round, bounded by
    # the existing per-family resource cap. This is acquisition fairness only:
    # it neither scores ETFs nor grants Observation/trade authority.
    offsets = {info_class: 0 for info_class in class_priority}
    for _ in range(MAX_OBSERVATION_INPUTS_PER_FAMILY):
        for info_class in class_priority:
            members = ordered[info_class]
            while offsets[info_class] < len(members):
                item = members[offsets[info_class]]
                offsets[info_class] += 1
                before = len(queue)
                append_item(item)
                if len(queue) > before:
                    break

    # Never strand unused validation capacity. Once bounded diversity has been
    # offered, retain the original class priority and deterministic hash order
    # for all remaining work.
    for info_class in class_priority:
        members = ordered[info_class]
        while offsets[info_class] < len(members):
            item = members[offsets[info_class]]
            offsets[info_class] += 1
            append_item(item)
    return queue

def _management_review_contract(code: str, managed_codes: set[str], held_codes: set[str]) -> dict[str, Any]:
    if code in held_codes:
        role = "HOLDING"
        required = ["CONTINUE_HOLDING", "REDUCE_REVIEW", "EXIT_REVIEW"]
    elif code in managed_codes:
        role = "MANAGED_MONITORING"
        required = ["RETAIN_OBSERVATION", "REMOVE_OBSERVATION", "OPPORTUNITY_REVIEW"]
    else:
        role = "NEW_DISCOVERY"
        required = ["RETAIN_DISCOVERY", "REJECT_NEW_DEPLOYMENT", "TRIAL_ELIGIBILITY_REVIEW"]
    return {
        "object_role": role,
        "direct_conclusion_required": True,
        "conclusion_status": "PENDING_MASTER_DECISION",
        "allowed_conclusions": required,
        "decision_owner": "MASTER_FORMAL_DECISION",
        "decision_boundary": "Discovery supplies object-level evidence and the required conclusion set; only MASTER Formal Decision confirms the final hold, reduce, exit, observation or Trial/Confirm action.",
    }


def _candidate(row: dict[str, Any], history: list[dict[str, Any]], required_history_end_date: str, management_review: dict[str, Any] | None = None) -> dict[str, Any] | None:
    completed = [x for x in history if str(x.get("date") or "") <= required_history_end_date]
    info_classes = list(row.get("_discovery_information_classes") or [])
    if len(completed) < MIN_HISTORY:
        if len(completed) < SHORT_HISTORY_MIN or not set(info_classes) & {"NEW_RELATIVE_DIVERGENCE", "SHORT_HISTORY_CURRENT_CHANGE", "EXTERNAL_DRIVER_TRIGGER"}:
            return None
        avg_amount = sum(float(x.get("amount") or 0) for x in completed[-20:]) / min(20, len(completed))
        return {
            "code": row["code"], "name": row["name"], "display_name": f'{row["name"]}（{row["code"]}）',
            "category": "OBSERVATION_EVALUATION_INPUT", "eligibility": "OBSERVATION_FULL_EVALUATION",
            "management_identity": None, "auto_promote_to_observation": False,
            "trial_confirm_permission": False, "trade_signal": None, "decision_output_generated": False,
            "discovery_semantic": "NODE_LOCAL_OBSERVATION_EVALUATION_INPUT",
            "entered_states": [], "surfaced_states": [],
            "information_classes": info_classes,
            "external_driver_evidence": list(row.get("_external_driver_triggers") or []),
            "discovery_spot": {k: row.get(k) for k in (
                "price", "change_pct", "amount", "amplitude_pct", "turnover_pct", "volume_ratio",
                "high", "low", "open", "prev_close", "return_60d_pct", "return_ytd_pct", "provider_timestamp"
            )},
            "historical_context": {
                "status": "SHORT_HISTORY_LIMITED", "as_of": completed[-1]["date"] if completed else None,
                "sample_count": len(completed), "listing_date": row.get("listing_date"),
                "avg_amount_20": round(avg_amount, 2),
                "limitation": "不足65个已完成交易日；不得推导完整历史趋势状态。",
            },
            "comparison_basis": ["当前相对结构", "有限历史", "成交与可执行性", "风险收益", "资本效率"],
            "decision_boundary": "短历史仅授予本节点Observation资格完整评估；不得替代正式行情、Observation ADMIT或MASTER，不产生Trial/Confirm、金额或交易动作。",
            "management_review": management_review or {},
        }
    current_states = classify_states(completed)
    previous_states = _state_names(completed[:-1]) if len(completed) > MIN_HISTORY else set()
    entered = sorted({x["state"] for x in current_states} - previous_states)
    aligned = [x for x in current_states if x["state"] in set(_potential_families(row))]
    relative_only = "NEW_RELATIVE_DIVERGENCE" in info_classes
    if not entered and not aligned and not relative_only and "EXTERNAL_DRIVER_TRIGGER" not in info_classes:
        return None
    avg_amount = sum(float(x.get("amount") or 0) for x in completed[-20:]) / min(20, len(completed))
    return {
        "code": row["code"], "name": row["name"], "display_name": f'{row["name"]}（{row["code"]}）',
        "category": "OBSERVATION_EVALUATION_INPUT", "eligibility": "OBSERVATION_FULL_EVALUATION",
        "management_identity": None, "auto_promote_to_observation": False,
        # Discovery is an actionable recommendation input by default.  This is
        # not an order, Trial, Confirm or permission change; it tells the
        # consumer to start from BUY and record an explicit counter-evidence
        # reason before downgrading the candidate.
        "default_recommendation": "BUY",
        "recommendation_status": "BUY_UNLESS_DISPROVED",
        "recommendation_boundary": "人工确认前的积极建议；不自动下单、不授予Trial/Confirm、不改变交易权限",
        "trial_confirm_permission": False, "trade_signal": None, "decision_output_generated": False,
        "discovery_semantic": "NODE_LOCAL_OBSERVATION_EVALUATION_INPUT",
        "entered_states": entered, "surfaced_states": current_states, "information_classes": info_classes,
        "external_driver_evidence": list(row.get("_external_driver_triggers") or []),
        "discovery_spot": {k: row.get(k) for k in (
            "price", "change_pct", "amount", "amplitude_pct", "turnover_pct", "volume_ratio",
            "high", "low", "open", "prev_close", "return_60d_pct", "return_ytd_pct", "provider_timestamp"
        )},
        "historical_context": {
            "status": "READY", "as_of": completed[-1]["date"], "sample_count": len(completed),
            "avg_amount_20": round(avg_amount, 2),
        },
        "comparison_basis": ["历史趋势/状态变化", "当前相对结构", "成交与可执行性", "风险收益", "资本效率"],
        "decision_boundary": "默认按买入建议起点进入正式判断；只有明确反证才降级或否决。该建议不自动产生Trial/Confirm、金额或交易动作。",
        "management_review": management_review or {},
    }



def attach_formal_quotes(discovery: dict[str, Any], market_quote: dict[str, Any]) -> dict[str, Any]:
    quotes = {}
    for quote in market_quote.get("quotes") or []:
        if not isinstance(quote, dict):
            continue
        symbol = str(quote.get("symbol") or quote.get("code") or "").upper().replace(".SH", "").replace(".SZ", "")
        if symbol:
            quotes[symbol] = quote
    candidates = []
    for item in discovery.get("candidates") or []:
        code = str(item.get("code") or "").upper()
        quote = quotes.get(code)
        quality = str((quote or {}).get("quality_status") or "").upper()
        freshness = str((quote or {}).get("freshness") or "").upper()
        usable = bool(quote and quality not in {"", "FAILED", "FAIL", "STALE", "INVALID"})
        if usable and freshness == "SESSION_REFERENCE":
            fitness = {
                "object_fact": True,
                "observation_evaluation": True,
                "capital_candidate_evaluation": True,
                "executable_price": False,
                "trade_amount_or_shares": False,
            }
            quote_status = "SESSION_REFERENCE_READY"
        elif usable:
            fitness = {
                "object_fact": True,
                "observation_evaluation": True,
                "capital_candidate_evaluation": True,
                "executable_price": freshness == "FRESH",
                "trade_amount_or_shares": freshness == "FRESH",
            }
            quote_status = "READY"
        else:
            fitness = {
                "object_fact": False,
                "observation_evaluation": False,
                "capital_candidate_evaluation": False,
                "executable_price": False,
                "trade_amount_or_shares": False,
            }
            quote_status = "UNAVAILABLE"
        candidates.append({
            **item,
            "formal_quote": quote or {},
            "formal_quote_status": quote_status,
            "formal_quote_evidence_fitness": fitness,
            "formal_quote_rule": "发现源只负责缩小评估对象；对象级正式行情由既有market_quote_router补采。SESSION_REFERENCE可支持Observation/候选资本评估，但不得冒充当前可执行价格、金额或份额依据。",
        })
    return {**discovery, "candidates": candidates, "formal_quote_coverage": sum(x["formal_quote_status"] in {"READY", "SESSION_REFERENCE_READY"} for x in candidates)}


def discover_formal_candidates(
    root: Path,
    *,
    market_date: str,
    required_history_end_date: str | None = None,
    managed_codes: set[str],
    held_codes: set[str] | None = None,
    spot_rows: list[dict[str, Any]] | None = None,
    history_by_code: dict[str, list[dict[str, Any]]] | None = None,
    external_driver_context: dict[str, Any] | None = None,
    verified_spot_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    generated_dt = datetime.now(BEIJING)
    generated = generated_dt.isoformat(timespec="seconds")
    if required_history_end_date is None:
        required_history_end_date = required_completed_history_date(root, generated_dt.date().isoformat())
    broad_started = time.monotonic()
    try:
        if spot_rows is not None:
            broad = list(spot_rows)
            broad_source_meta = {"injected_spot_rows": len(broad)}
        else:
            broad, broad_source_meta = fetch_reconciled_broad_etf_spot(market_date)
    except Exception as exc:
        return {
            "status": "DEGRADED", "generated_at_beijing": generated, "source": "EASTMONEY_PRIMARY_WITH_HITHINK_SECONDARY_FALLBACK",
            "broad_universe_count": 0, "candidates": [], "error": str(exc)[-500:],
            "broad_acquisition_elapsed_seconds": round(time.monotonic() - broad_started, 3),
            "decision_boundary": "广域发现失败不删除持仓/观察ETF，也不阻塞其现有正式MASTER链。",
        }
    broad_elapsed = round(time.monotonic() - broad_started, 3)
    # Keep the broad universe from the discovery provider, but overwrite any
    # overlapping objects with the verified A-share pulse facts.  The verified
    # snapshot is the node-local source of truth for price/change/amount; using
    # a fresh after-hours provider response here can otherwise make the same
    # close node reclassify intraday objects as if they disappeared.
    if verified_spot_rows:
        verified_by_code = {
            str(row.get("code") or row.get("symbol") or ""): row
            for row in verified_spot_rows
            if str(row.get("code") or row.get("symbol") or "")
        }
        merged = []
        for row in broad:
            code = str(row.get("code") or "")
            verified = verified_by_code.get(code)
            if not verified:
                merged.append(row)
                continue
            merged_row = dict(row)
            for source_key, target_key in (("close", "price"), ("change_pct", "change_pct"), ("amount", "amount"), ("volume", "volume"), ("high", "high"), ("low", "low"), ("open", "open"), ("prev_close", "prev_close")):
                if _num(verified.get(source_key)) is not None:
                    merged_row[target_key] = _num(verified.get(source_key))
            if verified.get("provider_name"):
                merged_row["name"] = str(verified["provider_name"])
            merged_row["verified_spot_source"] = "A_SHARE_VERIFIED_SNAPSHOT"
            merged.append(merged_row)
        broad = merged
        broad_source_meta["verified_spot_overlay_count"] = sum(1 for row in broad if row.get("verified_spot_source"))
    # Hyperliquid is a parallel continuous-market fact domain. It can widen
    # the evidence-acquisition queue for economically mapped ETFs, but it does
    # not create or promote Discovery candidates or grant trade authority.
    broad = _apply_external_driver_triggers(broad, external_driver_context)
    hyperliquid_contribution = build_contribution(context=external_driver_context or {}, task="DISCOVERY")
    prefilter_started = time.monotonic()
    held_codes = {str(x) for x in (held_codes or set())}
    prefiltered = _bounded_prefilter(broad, held_codes, market_date)
    prefilter_elapsed = round(time.monotonic() - prefilter_started, 3)
    max_history_attempts = min(len(prefiltered), MAX_HISTORY_SUCCESS_BUDGET * MAX_HISTORY_ATTEMPT_MULTIPLIER)
    candidates = []
    failures = []
    node_history = load_discovery_history_snapshot(root, market_date) if history_by_code is None else {}
    snapshot_dirty = False
    history_attempted = 0
    history_succeeded = 0
    history_reused = 0
    history_repair_attempted = 0
    started = time.monotonic()
    def resolve_history(row: dict[str, Any]) -> tuple[list[dict[str, Any]], str, bool, bool]:
        code = str(row["code"])
        history = (history_by_code or {}).get(code) if history_by_code is not None else node_history.get(code)
        if history_by_code is None and history is not None and not _history_is_current_for_required_date(history, required_history_end_date):
            history = None
        history_source = "INJECTED" if history_by_code is not None and history is not None else ("DISCOVERY_NODE_HISTORY" if history is not None else None)
        reused = history_source == "DISCOVERY_NODE_HISTORY"
        repair_attempted = False
        if history is None:
            history = load_validated_history(root, code, required_history_end_date, 90)
            if history is not None and not _history_is_current_for_required_date(history, required_history_end_date):
                history = None
            history_source = "VALIDATED_EXISTING_HISTORY" if history is not None else None
            reused = history is not None
        if history is None:
            repair_attempted = True
            history = fetch_daily_history(code, int(row.get("market_id") or 0), required_history_end_date, 90)
            provider = str((history[0] if history else {}).get("_provider") or "bounded_provider_repair").upper()
            history_source = f"{provider}_BOUNDED_REPAIR"
        return history, str(history_source or ""), reused, repair_attempted

    # Preserve deterministic queue semantics and the successful-evidence budget.
    # Only independent provider repairs inside the current queue window overlap.
    # A batch is never wider than the remaining success seats, so concurrency
    # cannot over-consume the 12-success resource contract.
    queue_index = 0
    while history_succeeded < MAX_HISTORY_SUCCESS_BUDGET and history_attempted < max_history_attempts and queue_index < len(prefiltered):
        remaining_success = MAX_HISTORY_SUCCESS_BUDGET - history_succeeded
        remaining_attempts = max_history_attempts - history_attempted
        batch_size = min(MAX_HISTORY_FETCH_WORKERS, remaining_success, remaining_attempts, len(prefiltered) - queue_index)
        batch = prefiltered[queue_index:queue_index + batch_size]
        queue_index += batch_size
        history_attempted += len(batch)

        resolved: dict[str, tuple[list[dict[str, Any]], str, bool, bool]] = {}
        errors: dict[str, Exception] = {}
        if history_by_code is not None or batch_size == 1:
            for row in batch:
                code = str(row["code"])
                try:
                    resolved[code] = resolve_history(row)
                except Exception as exc:
                    errors[code] = exc
        else:
            with ThreadPoolExecutor(max_workers=MAX_HISTORY_FETCH_WORKERS, thread_name_prefix="etf-discovery-history") as executor:
                futures = {executor.submit(resolve_history, row): str(row["code"]) for row in batch}
                for future in as_completed(futures):
                    code = futures[future]
                    try:
                        resolved[code] = future.result()
                    except Exception as exc:
                        errors[code] = exc

        # Consume completed work strictly in original queue order. Completion
        # order must never become a hidden ranking or change candidate identity.
        for row in batch:
            code = str(row["code"])
            if code in errors:
                failures.append({"code": code, "error": str(errors[code])[-300:]})
                continue
            history, history_source, reused, repair_attempted = resolved[code]
            if reused:
                history_reused += 1
            if repair_attempted:
                history_repair_attempted += 1
            if history_by_code is None and (history_source == "VALIDATED_EXISTING_HISTORY" or history_source.endswith("_BOUNDED_REPAIR")):
                node_history[code] = history
                snapshot_dirty = True
            history_succeeded += 1
            item = _candidate(row, history, required_history_end_date, _management_review_contract(code, managed_codes, held_codes))
            if item:
                item["history_source"] = history_source
                item_code = str(item.get("code") or "")
                item["management_identity"] = "MANAGED" if item_code in managed_codes else None
                item["discovery_semantic"] = (
                    "NODE_LOCAL_ALL_MARKET_OPPORTUNITY_SIGNAL_FOR_EXISTING_MANAGED_ETF"
                    if item_code in managed_codes
                    else "NODE_LOCAL_OBSERVATION_EVALUATION_INPUT"
                )
                candidates.append(item)
    if history_by_code is None and snapshot_dirty:
        persist_discovery_history_snapshot(root, market_date, node_history)
    elapsed = round(time.monotonic() - started, 3)
    # The prefilter already bounds provider work.  Do not re-rank surviving
    # opportunities by state or liquidity: that would recreate a hidden Top-N.
    candidates.sort(key=lambda item: str(item.get("code") or ""))
    candidates = candidates[:MAX_OBSERVATION_CANDIDATES]
    # A broad snapshot with no prefiltered objects is not a completed empty
    # Discovery. It means the current node did not provide enough executable
    # fields to start the required historical validation, so it must remain
    # visibly degraded and must not be consumed as a legal empty set.
    empty_reason = ""
    if not prefiltered:
        empty_reason = "NO_ELIGIBLE_OBJECTS_FOR_HISTORY_VALIDATION"
    coverage_status = (
        "INCOMPLETE" if not prefiltered
        else ("COMPLETE" if not failures and not broad_source_meta.get("fallback_used") else ("UNAVAILABLE" if history_succeeded == 0 else "PARTIAL"))
    )
    return {
        "schema_version": "1.0", "status": "READY" if broad and prefiltered and not failures and not broad_source_meta.get("fallback_used") else "DEGRADED",
        "generated_at_beijing": generated, "market_date": market_date,
        "required_history_end_date": required_history_end_date,
        "source": "EASTMONEY_PRIMARY_HITHINK_SECONDARY_UNIVERSE_PLUS_VERIFIED_NODE_OVERLAY_AND_BOUNDED_HISTORY_REPAIR",
        "broad_source_reconciliation": broad_source_meta,
        "source_role": "DISCOVERY_ONLY; formal trade decision remains MASTER-owned",
        "broad_universe_count": len(broad),
        "managed_identity_count": sum(1 for x in broad if x.get("code") in managed_codes),
        "held_identity_count": sum(1 for x in broad if x.get("code") in held_codes),
        "managed_excluded_count": 0,
        "history_prefilter_count": len(prefiltered), "candidate_count": len(candidates),
        "history_success_budget": MAX_HISTORY_SUCCESS_BUDGET, "history_attempt_cap": max_history_attempts, "history_fetch_workers": MAX_HISTORY_FETCH_WORKERS,
        "history_attempted_count": history_attempted, "history_succeeded_count": history_succeeded,
        "history_reused_count": history_reused, "history_repair_attempted_count": history_repair_attempted,
        "history_failure_count": len(failures), "history_elapsed_seconds": elapsed,
        "empty_reason": empty_reason,
        "latency_observability": {
            "broad_acquisition_elapsed_seconds": broad_elapsed,
            "prefilter_elapsed_seconds": prefilter_elapsed,
            "history_validation_elapsed_seconds": elapsed,
            "measurement_role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
        },
        "coverage_status": coverage_status,
        "history_failures": failures, "candidates": candidates,
        "observation_capacity": {"target_typical": "5-10", "allowed_min": 0, "resource_protection_max": MAX_OBSERVATION_CANDIDATES},
        "selection_contract": {
            "all_market_boundary": True,
            "no_gain_ranking": True,
            "no_hidden_score": True,
            "prefilter": "single broad cross-section; minimum executability; explicit information-acquisition classes for relative divergence, structural change, short-history current change and persistent structure; exact cheap-label clone control; no return ranking; date hash is tie-break only within equivalent information class/exposure",
            "final_ingress": "history budget counts successful evidence acquisition, with a bounded attempt cap; provider failure is explicit, does not consume a successful-evidence seat, and does not convert successful peers into legal capital competitors",
        },
        "decision_boundary": "发现对象是本节点临时正式评估输入，不是第三种ETF身份；不得自动写观察池、生成Trial/Confirm或交易动作。只有在广域获取、预筛选和所需历史验证均完成时，candidate_count=0才表示合法空集合；empty_reason存在或coverage_status不是COMPLETE时，必须按发现未完成/降级处理。",
        "hyperliquid_contribution": hyperliquid_contribution,
    }
