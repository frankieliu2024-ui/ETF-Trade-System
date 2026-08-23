from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


BASE_URL = "https://fuyao.aicubes.cn"
SHANGHAI = ZoneInfo("Asia/Shanghai")
STANDARD_COLUMNS = [
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude_pct",
    "change_pct",
    "change",
    "turnover_pct",
]


class HithinkAPIError(RuntimeError):
    def __init__(self, code: int | str, message: str, request_id: str | None = None):
        self.code = code
        self.request_id = request_id
        detail = f"hithink code={code}: {message}"
        if request_id:
            detail += f"; request_id={request_id}"
        super().__init__(detail)


def _load_api_key() -> str:
    value = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
    if value:
        return value

    appdata = os.environ.get("APPDATA")
    if appdata:
        credential_path = Path(appdata) / "hithink-finance" / "credentials.env"
        if credential_path.exists():
            for line in credential_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("HITHINK_FINANCE_API_KEY="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value

    raise RuntimeError(
        "HITHINK_FINANCE_API_KEY is not configured in the process environment "
        "or the user credentials file"
    )


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=SHANGHAI)
        return value.astimezone(SHANGHAI)
    return datetime(value.year, value.month, value.day, tzinfo=SHANGHAI)


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)


@dataclass
class HithinkETFClient:
    timeout: int = 20
    max_attempts: int = 3

    def __post_init__(self) -> None:
        self._api_key = _load_api_key()

    def _request(
        self,
        path: str,
        params: dict[str, Any],
        raw_dir: Path | None = None,
        raw_label: str = "response",
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "X-api-key": self._api_key,
                    "Accept": "application/json",
                    "User-Agent": "ETF-Swing-System/2.2.14",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 429 or 500 <= exc.code <= 599:
                    if attempt < self.max_attempts:
                        time.sleep(0.8 * (2 ** (attempt - 1)))
                        continue
                raise HithinkAPIError(exc.code, f"HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    time.sleep(0.8 * (2 ** (attempt - 1)))
                    continue
                raise RuntimeError(f"hithink request failed after {attempt} attempts: {exc}") from exc

            code = payload.get("code")
            if code == 0:
                if raw_dir is not None:
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    request_id = payload.get("request_id") or "no-request-id"
                    filename = f"{_safe_name(raw_label)}_{_safe_name(str(request_id))}.json"
                    (raw_dir / filename).write_text(
                        json.dumps(
                            {
                                "request": {"path": path, "params": params},
                                "collected_at": datetime.now(SHANGHAI).isoformat(),
                                "response": payload,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                return payload

            message = str(payload.get("message") or "business request failed")
            request_id = payload.get("request_id")
            retryable = code == 4001 or code in {5001, 5002, 5003}
            if retryable and attempt < self.max_attempts:
                time.sleep(0.8 * (2 ** (attempt - 1)))
                continue
            raise HithinkAPIError(code, message, request_id)

        raise RuntimeError(f"hithink request failed: {last_error}")

    def search_etf(self, query: str, raw_dir: Path | None = None) -> dict[str, Any]:
        payload = self._request(
            "/api/meta/tickers/search",
            {"q": query, "asset_type": "fund-etf", "limit": 10},
            raw_dir,
            f"search_{query}",
        )
        items = payload["data"]["item"]
        exact = [item for item in items if item.get("ticker") == query or item.get("thscode") == query]
        candidates = exact or items
        if len(candidates) != 1:
            names = [f"{item.get('thscode')} {item.get('name')}" for item in candidates]
            raise RuntimeError(f"ETF symbol is ambiguous for {query}: {names}")
        item = candidates[0]
        if item.get("asset_type") != "fund-etf":
            raise RuntimeError(f"Expected fund-etf for {query}, got {item.get('asset_type')}")
        return item

    def snapshot(self, thscode: str, raw_dir: Path | None = None) -> dict[str, Any]:
        payload = self._request(
            "/api/fund/market/snapshot",
            {"thscode": thscode},
            raw_dir,
            f"snapshot_{thscode}",
        )
        items = payload["data"]["item"]
        if len(items) != 1 or items[0].get("thscode") != thscode:
            raise RuntimeError(f"Unexpected snapshot payload for {thscode}")
        return {
            **items[0],
            "source_timestamp": payload["data"].get("timestamp"),
            "request_id": payload.get("request_id"),
        }

    def history(
        self,
        thscode: str,
        start: date | datetime,
        end: date | datetime,
        raw_dir: Path | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        start_dt = _as_datetime(start)
        end_dt = _as_datetime(end)
        if end_dt.time() == datetime.min.time():
            end_dt = end_dt + timedelta(days=1) - timedelta(milliseconds=1)
        if end_dt < start_dt:
            raise ValueError("end must not be earlier than start")

        rows: list[dict[str, Any]] = []
        request_ids: list[str] = []
        cursor = start_dt
        windows = 0
        while cursor <= end_dt:
            window_end = min(_add_years(cursor, 5) - timedelta(milliseconds=1), end_dt)
            start_ms = int(cursor.timestamp() * 1000)
            end_ms = int(window_end.timestamp() * 1000)
            payload = self._request(
                "/api/fund/market/historical",
                {
                    "thscode": thscode,
                    "interval": "1d",
                    "start": start_ms,
                    "end": end_ms,
                },
                raw_dir,
                f"history_{thscode}_{start_ms}_{end_ms}",
            )
            data = payload["data"]
            if data.get("thscode") != thscode or data.get("interval") != "1d":
                raise RuntimeError(f"Unexpected history payload identity for {thscode}")
            rows.extend(data.get("item") or [])
            if payload.get("request_id"):
                request_ids.append(payload["request_id"])
            windows += 1
            cursor = window_end + timedelta(milliseconds=1)

        frame = pd.DataFrame(rows)
        if frame.empty:
            frame = pd.DataFrame(columns=STANDARD_COLUMNS)
        else:
            frame = frame.rename(
                columns={
                    "date_ms": "date_ms",
                    "open_price": "open",
                    "high_price": "high",
                    "low_price": "low",
                    "close_price": "close",
                    "turnover": "amount",
                }
            )
            frame["date"] = (
                pd.to_datetime(frame["date_ms"], unit="ms", utc=True)
                .dt.tz_convert("Asia/Shanghai")
                .dt.tz_localize(None)
                .dt.normalize()
            )
            for column in ["open", "high", "low", "close", "volume", "amount"]:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            previous_close = frame["close"].shift(1)
            frame["change"] = frame["close"] - previous_close
            frame["change_pct"] = frame["change"] / previous_close * 100
            frame["amplitude_pct"] = (frame["high"] - frame["low"]) / previous_close * 100
            frame["turnover_pct"] = pd.NA
            frame = (
                frame.sort_values("date")
                .drop_duplicates(subset=["date"], keep="last")
                .reset_index(drop=True)
            )
            frame = frame[STANDARD_COLUMNS]

        meta = {
            "source": "同花顺金融数据API",
            "endpoint": "/api/fund/market/historical",
            "thscode": thscode,
            "interval": "1d",
            "adjustment": "接口不提供复权参数，按ETF交易所原始价格使用",
            "windows": windows,
            "request_ids": request_ids,
            "rows": len(frame),
        }
        return frame, meta
