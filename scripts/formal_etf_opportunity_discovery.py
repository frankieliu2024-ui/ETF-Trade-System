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

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
SPOT_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
SSE_MASTER_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_MASTER_SQL = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SZSE_MASTER_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_MASTER_CATALOG = "1945"
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


def _plain_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).strip()


def _six_digit(value: Any) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", _plain_html(value))
    return match.group(1) if match else ""


def fetch_sse_official_etf_master(market_date: str) -> list[dict[str, Any]]:
    """Enumerate SSE ETFs from the existing official broad ETF-scale owner."""
    payload = _request_json_headers(
        SSE_MASTER_URL,
        {
            "isPagination": "true", "pageHelp.pageSize": "2000", "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1", "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
            "sqlId": SSE_MASTER_SQL, "STAT_DATE": market_date,
        },
        {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
         "Accept": "application/json, text/javascript, */*; q=0.01", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
         "X-Requested-With": "XMLHttpRequest",
         "Referer": "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"},
        timeout=15,
    )
    rows = payload.get("result") or (payload.get("pageHelp") or {}).get("data") or []
    result = []
    for raw in rows:
        code = str(raw.get("SEC_CODE") or "").strip()
        if re.fullmatch(r"\d{6}", code):
            result.append({"code": code, "name": str(raw.get("SEC_NAME") or code).strip(),
                           "market_id": 1, "exchange": "SSE", "identity_source": "SSE_OFFICIAL_ETF_SCALE_ENUMERATION"})
    return result


def fetch_szse_official_etf_master() -> list[dict[str, Any]]:
    """Enumerate the official SZSE ETF List (CATALOGID=1945) with explicit pagination."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.szse.cn/market/product/list/etfList/index.html",
    }
    result: list[dict[str, Any]] = []
    page = 1
    expected_records: int | None = None
    while True:
        payload = _request_json_headers(
            SZSE_MASTER_URL,
            {"SHOWTYPE": "JSON", "CATALOGID": SZSE_MASTER_CATALOG, "TABKEY": "tab1", "tab1PAGENO": str(page)},
            headers, timeout=15,
        )
        block = payload[0] if isinstance(payload, list) and payload else {}
        if block.get("error"):
            raise RuntimeError(f"SZSE ETF List API error: {block.get('error')}")
        metadata = block.get("metadata") or {}
        if str(metadata.get("catalogid") or "") != SZSE_MASTER_CATALOG:
            raise RuntimeError("SZSE ETF List returned unexpected catalog")
        pages = int(metadata.get("pagecount") or 1)
        if expected_records is None:
            expected_records = int(metadata.get("recordcount") or 0)
        for raw in block.get("data") or []:
            code = _six_digit(raw.get("sys_key"))
            name = _plain_html(raw.get("zxjghj"))
            if not code:
                continue
            if name.startswith(code):
                name = name[len(code):].strip()
            result.append({
                "code": code, "name": name or code, "market_id": 0, "exchange": "SZSE",
                "identity_source": "SZSE_OFFICIAL_ETF_LIST_1945",
                "tracking_index": _plain_html(raw.get("nhzs")),
                "manager": _plain_html(raw.get("glrmc")),
            })
        if page >= pages:
            break
        page += 1
        if page > 200:
            raise RuntimeError("SZSE ETF List pagination exceeded safety bound")
    unique = {row["code"]: row for row in result}
    if expected_records and len(unique) != expected_records:
        raise RuntimeError(f"SZSE ETF List coverage mismatch: expected={expected_records} parsed={len(unique)}")
    return [unique[code] for code in sorted(unique)]


def fetch_hithink_etf_master() -> list[dict[str, Any]]:
    """Enumerate the Hithink ETF category as an independent security-master fallback."""
    req = Request(
        HITHINK_ETF_MASTER_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://fund.10jqka.com.cn/datacenter/jz/kfs/etf/",
            "Accept": "application/json,text/javascript,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8", errors="replace").strip()
    if text.startswith("g(") and text.endswith(")"):
        text = text[2:-1]
    elif text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    payload = json.loads(text)
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


def fetch_official_etf_master(market_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enumerate SSE/SZSE independently so one exchange outage cannot erase the other."""
    rows: list[dict[str, Any]] = []
    errors: dict[str, str | None] = {"sse": None, "szse": None}
    counts = {"sse": 0, "szse": 0}
    try:
        sse_rows = fetch_sse_official_etf_master(market_date)
        rows.extend(sse_rows)
        counts["sse"] = len(sse_rows)
    except Exception as exc:
        errors["sse"] = str(exc)[-300:]
    try:
        szse_rows = fetch_szse_official_etf_master()
        rows.extend(szse_rows)
        counts["szse"] = len(szse_rows)
    except Exception as exc:
        errors["szse"] = str(exc)[-300:]
    unique = {(row["market_id"], row["code"]): row for row in rows}
    if not unique:
        raise RuntimeError(f"official ETF masters unavailable; sse={errors['sse']}; szse={errors['szse']}")
    return [unique[key] for key in sorted(unique)], {
        "sse_official_master_count": counts["sse"],
        "szse_official_master_count": counts["szse"],
        "sse_official_master_error": errors["sse"],
        "szse_official_master_error": errors["szse"],
    }


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


def fetch_official_tencent_broad_spot(market_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Independent ETF identity fallback + Tencent hydration; partial batches degrade explicitly."""
    try:
        from scripts.tencent_quote import fetch_tencent_quotes
    except ModuleNotFoundError:
        # market-snapshot executes this owner both as an imported package and from
        # repository script context; keep the existing Tencent owner reachable in both.
        from tencent_quote import fetch_tencent_quotes

    hithink_error = None
    official_error = None
    master_source = "HITHINK_ETF_CATEGORY"
    try:
        master = fetch_hithink_etf_master()
        master_meta = {"hithink_master_count": len(master)}
    except Exception as exc:
        hithink_error = str(exc)[-300:]
        master_source = "SSE_SZSE_OFFICIAL_FALLBACK"
        try:
            master, master_meta = fetch_official_etf_master(market_date)
        except Exception as official_exc:
            official_error = str(official_exc)[-300:]
            raise RuntimeError(
                f"independent ETF masters unavailable; hithink={hithink_error}; official={official_error}"
            ) from official_exc
    master_meta = {
        **master_meta,
        "identity_master_source": master_source,
        "hithink_master_error": hithink_error,
        "official_master_fallback_error": official_error,
    }
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for offset in range(0, len(master), 60):
        batch = master[offset:offset + 60]
        symbols = [f'{x["code"]}.{"SH" if x["market_id"] == 1 else "SZ"}' for x in batch]
        try:
            quotes = fetch_tencent_quotes(symbols, timeout=10)
        except Exception as exc:
            failures.append({"offset": offset, "count": len(batch), "error": str(exc)[-240:]})
            continue
        for identity, symbol in zip(batch, symbols):
            quote = quotes.get(symbol.upper())
            if quote:
                rows.append(_tencent_spot_row(identity, quote))
    return rows, {
        **master_meta,
        "official_master_count": len(master), "tencent_quote_count": len(rows),
        "tencent_failed_batch_count": len(failures), "tencent_failures": failures,
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
    """High-recall dual-source universe; either source can keep Discovery available.

    Eastmoney contributes its broad current cross-section. Hithink independently
    contributes ETF identities. When both work, only Hithink identities absent
    from Eastmoney are hydrated through Tencent before entering the cheap
    Discovery gate. This prevents a successful Eastmoney response from becoming
    a hidden completeness boundary without paying for full duplicate hydration.
    """
    east_rows: list[dict[str, Any]] = []
    hithink_master: list[dict[str, Any]] = []
    east_error = None
    hithink_error = None

    def get_east() -> list[dict[str, Any]]:
        return fetch_broad_etf_spot()

    def get_hithink() -> list[dict[str, Any]]:
        return fetch_hithink_etf_master()

    source_started = time.monotonic()
    source_finished: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="etf-universe") as executor:
        future_sources = {executor.submit(get_east): "eastmoney", executor.submit(get_hithink): "hithink"}
        for future in as_completed(future_sources):
            source = future_sources[future]
            source_finished[source] = round(time.monotonic() - source_started, 3)
            try:
                value = future.result()
                if source == "eastmoney":
                    east_rows = value
                else:
                    hithink_master = value
            except Exception as exc:
                if source == "eastmoney":
                    east_error = str(exc)[-500:]
                else:
                    hithink_error = str(exc)[-500:]
    source_join_elapsed = round(time.monotonic() - source_started, 3)

    if not east_rows and not hithink_master:
        # Preserve the existing deeper official-master fallback when both normal
        # independent universe sources are unavailable.
        fallback_rows, fallback_meta = fetch_official_tencent_broad_spot(market_date)
        if not fallback_rows:
            raise RuntimeError(
                f"broad providers unavailable; eastmoney={east_error}; hithink={hithink_error}"
            )
        return fallback_rows, {
            **fallback_meta,
            "universe_mode": "DEEP_FALLBACK",
            "eastmoney_count": 0,
            "hithink_identity_count": 0,
            "eastmoney_error": east_error,
            "hithink_error": hithink_error,
            "fallback_used": True,
            "fallback_source": "OFFICIAL_MASTER_PLUS_TENCENT",
            "reconciled_count": len(fallback_rows),
            "source_elapsed_seconds": source_finished,
            "source_join_elapsed_seconds": source_join_elapsed,
        }

    if not east_rows:
        hydrated, hydration_meta = _hydrate_tencent_identities(hithink_master)
        if not hydrated:
            raise RuntimeError(f"Hithink universe available but Tencent hydration unavailable; eastmoney={east_error}")
        return hydrated, {
            **hydration_meta,
            "universe_mode": "HITHINK_ONLY",
            "eastmoney_count": 0,
            "hithink_identity_count": len(hithink_master),
            "eastmoney_error": east_error,
            "hithink_error": hithink_error,
            "hithink_only_identity_count": len(hithink_master),
            "fallback_used": True,
            "fallback_source": "HITHINK_ETF_MASTER_PLUS_TENCENT",
            "reconciled_count": len(hydrated),
            "source_elapsed_seconds": source_finished,
            "source_join_elapsed_seconds": source_join_elapsed,
        }

    if not hithink_master:
        return east_rows, {
            "universe_mode": "EASTMONEY_ONLY_DEGRADED_RECONCILIATION",
            "eastmoney_count": len(east_rows),
            "hithink_identity_count": 0,
            "eastmoney_error": east_error,
            "hithink_error": hithink_error,
            "hithink_only_identity_count": 0,
            "fallback_used": False,
            "fallback_source": None,
            "reconciled_count": len(east_rows),
            "source_elapsed_seconds": source_finished,
            "source_join_elapsed_seconds": source_join_elapsed,
        }

    east_keys = {(int(x.get("market_id") or 0), str(x.get("code") or "")) for x in east_rows}
    missing = [
        x for x in hithink_master
        if (int(x.get("market_id") or 0), str(x.get("code") or "")) not in east_keys
    ]
    supplemental_started = time.monotonic()
    supplemental_rows, hydration_meta = _hydrate_tencent_identities(missing) if missing else ([], {
        "requested_identity_count": 0, "tencent_quote_count": 0,
        "tencent_failed_batch_count": 0, "tencent_failures": [],
    })
    supplemental_hydration_elapsed = round(time.monotonic() - supplemental_started, 3)
    merged = {(int(x.get("market_id") or 0), str(x.get("code") or "")): x for x in east_rows}
    for row in supplemental_rows:
        merged[(int(row.get("market_id") or 0), str(row.get("code") or ""))] = row
    rows = [merged[key] for key in sorted(merged)]
    return rows, {
        **hydration_meta,
        "universe_mode": "DUAL_SOURCE_RECONCILED",
        "eastmoney_count": len(east_rows),
        "hithink_identity_count": len(hithink_master),
        "eastmoney_error": east_error,
        "hithink_error": hithink_error,
        "hithink_only_identity_count": len(missing),
        "hithink_only_hydrated_count": len(supplemental_rows),
        "fallback_used": False,
        "fallback_source": None,
        "reconciled_count": len(rows),
        "source_elapsed_seconds": source_finished,
        "source_join_elapsed_seconds": source_join_elapsed,
        "supplemental_hydration_elapsed_seconds": supplemental_hydration_elapsed,
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
    returns = {w: _ret(closes, w) for w in (20, 40, 60)}
    positives = [w for w, value in returns.items() if value is not None and value > 0]
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
        "NEW_RELATIVE_DIVERGENCE",
        "STRUCTURAL_CHANGE",
        "SHORT_HISTORY_CURRENT_CHANGE",
        "PERSISTENT_STRUCTURE",
    )
    broad_median = _broad_return_median(rows)
    buckets: dict[str, dict[str, dict[str, Any]]] = {x: {} for x in class_priority}
    for row in rows:
        code = str(row.get("code") or "")
        if code in held_codes:
            continue
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

def _candidate(row: dict[str, Any], history: list[dict[str, Any]], required_history_end_date: str) -> dict[str, Any] | None:
    completed = [x for x in history if str(x.get("date") or "") <= required_history_end_date]
    info_classes = list(row.get("_discovery_information_classes") or [])
    if len(completed) < MIN_HISTORY:
        if len(completed) < SHORT_HISTORY_MIN or not set(info_classes) & {"NEW_RELATIVE_DIVERGENCE", "SHORT_HISTORY_CURRENT_CHANGE"}:
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
        }
    current_states = classify_states(completed)
    previous_states = _state_names(completed[:-1]) if len(completed) > MIN_HISTORY else set()
    entered = sorted({x["state"] for x in current_states} - previous_states)
    aligned = [x for x in current_states if x["state"] in set(_potential_families(row))]
    relative_only = "NEW_RELATIVE_DIVERGENCE" in info_classes
    if not entered and not aligned and not relative_only:
        return None
    avg_amount = sum(float(x.get("amount") or 0) for x in completed[-20:]) / min(20, len(completed))
    return {
        "code": row["code"], "name": row["name"], "display_name": f'{row["name"]}（{row["code"]}）',
        "category": "OBSERVATION_EVALUATION_INPUT", "eligibility": "OBSERVATION_FULL_EVALUATION",
        "management_identity": None, "auto_promote_to_observation": False,
        "trial_confirm_permission": False, "trade_signal": None, "decision_output_generated": False,
        "discovery_semantic": "NODE_LOCAL_OBSERVATION_EVALUATION_INPUT",
        "entered_states": entered, "surfaced_states": current_states, "information_classes": info_classes,
        "discovery_spot": {k: row.get(k) for k in (
            "price", "change_pct", "amount", "amplitude_pct", "turnover_pct", "volume_ratio",
            "high", "low", "open", "prev_close", "return_60d_pct", "return_ytd_pct", "provider_timestamp"
        )},
        "historical_context": {
            "status": "READY", "as_of": completed[-1]["date"], "sample_count": len(completed),
            "avg_amount_20": round(avg_amount, 2),
        },
        "comparison_basis": ["历史趋势/状态变化", "当前相对结构", "成交与可执行性", "风险收益", "资本效率"],
        "decision_boundary": "仅取得本节点Observation资格完整评估；Observation身份只能由同节点正式决策ADMIT/RETAIN形成，发现本身不产生Trial/Confirm、金额或交易动作。",
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
        usable = bool(quote and quality not in {"", "FAILED", "FAIL", "STALE", "INVALID"})
        candidates.append({
            **item,
            "formal_quote": quote or {},
            "formal_quote_status": "READY" if usable else "UNAVAILABLE",
            "formal_quote_rule": "发现源只负责缩小评估对象；正式当前行情必须由现有market_quote_router对象级补采链取得。",
        })
    return {**discovery, "candidates": candidates, "formal_quote_coverage": sum(x["formal_quote_status"] == "READY" for x in candidates)}


def discover_formal_candidates(
    root: Path,
    *,
    market_date: str,
    required_history_end_date: str | None = None,
    managed_codes: set[str],
    held_codes: set[str] | None = None,
    spot_rows: list[dict[str, Any]] | None = None,
    history_by_code: dict[str, list[dict[str, Any]]] | None = None,
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
            "status": "DEGRADED", "generated_at_beijing": generated, "source": "DUAL_SOURCE_ETF_UNIVERSE_WITH_INDEPENDENT_FALLBACK",
            "broad_universe_count": 0, "candidates": [], "error": str(exc)[-500:],
            "broad_acquisition_elapsed_seconds": round(time.monotonic() - broad_started, 3),
            "decision_boundary": "广域发现失败不删除持仓/观察ETF，也不阻塞其现有正式MASTER链。",
        }
    broad_elapsed = round(time.monotonic() - broad_started, 3)
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
            item = _candidate(row, history, required_history_end_date)
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
    return {
        "schema_version": "1.0", "status": "READY" if broad and not failures else "DEGRADED",
        "generated_at_beijing": generated, "market_date": market_date,
        "required_history_end_date": required_history_end_date,
        "source": "EASTMONEY_HITHINK_DUAL_SOURCE_UNIVERSE_PLUS_BOUNDED_HISTORY_REPAIR",
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
        "latency_observability": {
            "broad_acquisition_elapsed_seconds": broad_elapsed,
            "prefilter_elapsed_seconds": prefilter_elapsed,
            "history_validation_elapsed_seconds": elapsed,
            "measurement_role": "OBSERVABILITY_ONLY_NOT_DECISION_GATE",
        },
        "coverage_status": "COMPLETE" if not failures else ("UNAVAILABLE" if history_succeeded == 0 else "PARTIAL"),
        "history_failures": failures, "candidates": candidates,
        "observation_capacity": {"target_typical": "5-10", "allowed_min": 0, "resource_protection_max": MAX_OBSERVATION_CANDIDATES},
        "selection_contract": {
            "all_market_boundary": True,
            "no_gain_ranking": True,
            "no_hidden_score": True,
            "prefilter": "single broad cross-section; minimum executability; explicit information-acquisition classes for relative divergence, structural change, short-history current change and persistent structure; exact cheap-label clone control; no return ranking; date hash is tie-break only within equivalent information class/exposure",
            "final_ingress": "history budget counts successful evidence acquisition, with a bounded attempt cap; provider failure is explicit, does not consume a successful-evidence seat, and does not convert successful peers into legal capital competitors",
        },
        "decision_boundary": "发现对象是本节点临时正式评估输入，不是第三种ETF身份；不得自动写观察池、生成Trial/Confirm或交易动作。",
    }
