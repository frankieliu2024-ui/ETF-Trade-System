from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import time
import zipfile
from calendar import FRIDAY, monthcalendar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    from state_manager import read_json
except ModuleNotFoundError:
    from scripts.state_manager import read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "data/state/if_ic_basis_5d_evidence.json"
CONCLUSION = ROOT / "research/backtests/index_futures_if_ic_basis_formal_conclusion.json"
CALENDAR = ROOT / "config/market/a_share_trading_calendar_2026.json"
PRODUCTS = {"IF": "000300", "IC": "000905"}
HEADERS = {"User-Agent": "Mozilla/5.0 ETF-Trade-System formal research evidence", "Accept": "*/*"}
BJ = ZoneInfo("Asia/Shanghai")


def _num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _is_trading_day(d: date, calendar: dict) -> bool:
    return d.weekday() < 5 and d.isoformat() not in set(calendar.get("closed_dates") or [])


def _decision_market_date(root: Path) -> date:
    current = read_json(root / "data/state/CURRENT.json", {})
    cal = read_json(root / CALENDAR.relative_to(ROOT), {})
    now_bj = datetime.now(timezone.utc).astimezone(BJ)
    today = now_bj.date()
    if _is_trading_day(today, cal):
        return today
    raw = str(current.get("market_date") or "")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        d = today
        while not _is_trading_day(d, cal):
            d -= timedelta(days=1)
        return d


def _month_keys(start: date, end: date) -> list[str]:
    y, m = start.year, start.month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}{m:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def _third_friday(year: int, month: int) -> date:
    fridays = [week[FRIDAY] for week in monthcalendar(year, month) if week[FRIDAY]]
    return date(year, month, fridays[2])


def _contract_month(contract: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(?:IF|IC)(\d{4})", contract.upper())
    if not m:
        return None
    yymm = m.group(1)
    return 2000 + int(yymm[:2]), int(yymm[2:])


def _urlopen_bytes(url: str, timeout: int = 22) -> bytes:
    req = Request(url, headers=HEADERS)
    return urlopen(req, timeout=timeout).read()


def _fetch_month(ym: str, start: date, end: date) -> list[dict]:
    last = None
    urls = [
        f"https://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip",
        f"http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip",
    ]
    for attempt in range(4):
        for url in urls:
            try:
                raw = _urlopen_bytes(url)
                rows = []
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for name in zf.namelist():
                        m = re.fullmatch(r"(\d{8})_1\.csv", name.split("/")[-1])
                        if not m:
                            continue
                        d = datetime.strptime(m.group(1), "%Y%m%d").date()
                        if d < start or d > end:
                            continue
                        text = zf.read(name).decode("gb2312", errors="ignore")
                        reader = csv.reader(io.StringIO(text))
                        header = next(reader, [])
                        if not header or "合约代码" not in header[0]:
                            continue
                        for parts in reader:
                            if len(parts) < 10:
                                continue
                            contract = str(parts[0]).strip().upper()
                            if not re.fullmatch(r"(?:IF|IC)\d{4}", contract):
                                continue
                            close = _num(parts[8])
                            settlement = _num(parts[9])
                            volume = _num(parts[4])
                            oi = _num(parts[6])
                            if None in (close, settlement, volume, oi):
                                continue
                            rows.append({
                                "trade_date": d,
                                "product": contract[:2],
                                "contract": contract,
                                "close": close,
                                "settlement": settlement,
                                "volume": volume,
                                "open_interest": oi,
                            })
                return rows
            except Exception as exc:
                last = exc
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"CFFEX archive fetch failed {ym}: {type(last).__name__}: {last}")


def _fetch_futures(start: date, end: date) -> list[dict]:
    out = []
    errors = []
    for ym in _month_keys(start, end):
        try:
            out.extend(_fetch_month(ym, start, end))
        except Exception as exc:
            errors.append(str(exc))
    if not out:
        raise RuntimeError("; ".join(errors) or "no CFFEX rows")
    dedup = {}
    for row in out:
        dedup[(row["trade_date"], row["product"], row["contract"])] = row
    return sorted(dedup.values(), key=lambda x: (x["trade_date"], x["product"], x["contract"]))


def _fetch_spot(code: str, start: date, end: date) -> dict[date, float]:
    params = {
        "secid": f"1.{code}",
        "klt": "101",
        "fqt": "0",
        "beg": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "lmt": "300",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
    last = None
    for attempt in range(4):
        try:
            payload = json.loads(_urlopen_bytes(url, 18).decode("utf-8"))
            klines = ((payload.get("data") or {}).get("klines") or [])
            result = {}
            for line in klines:
                p = str(line).split(",")
                if len(p) < 3:
                    continue
                try:
                    d = date.fromisoformat(p[0])
                except ValueError:
                    continue
                close = _num(p[2])
                if close is not None and close > 0:
                    result[d] = close
            if result:
                return result
            raise RuntimeError("empty spot history")
        except Exception as exc:
            last = exc
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"spot history failed {code}: {type(last).__name__}: {last}")


def _expiry_map(rows: list[dict], cutoff: date) -> dict[str, date]:
    by_contract: dict[str, list[date]] = {}
    for row in rows:
        by_contract.setdefault(row["contract"], []).append(row["trade_date"])
    out = {}
    for contract, dates in by_contract.items():
        ym = _contract_month(contract)
        if not ym:
            continue
        nominal = _third_friday(*ym)
        if nominal <= cutoff:
            after_or_on_nominal = [d for d in dates if d >= nominal]
            out[contract] = max(after_or_on_nominal) if after_or_on_nominal else nominal
        else:
            out[contract] = nominal
    return out


def _contract_basis_history(rows: list[dict], spot: dict[date, float]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        s = spot.get(row["trade_date"])
        if not s:
            continue
        basis = (row["close"] / s - 1.0) * 100.0
        out.setdefault(row["contract"], []).append({**row, "spot_close": s, "basis_close_pct": basis})
    for values in out.values():
        values.sort(key=lambda x: x["trade_date"])
        for i, row in enumerate(values):
            row["basis_change_5d_pct_points"] = row["basis_close_pct"] - values[i - 5]["basis_close_pct"] if i >= 5 else None
    return out


def _select(product: str, mode: str, trade_date: date, rows: list[dict], expiry: dict[str, date], history: dict[str, list[dict]]) -> dict:
    candidates = [x for x in rows if x["product"] == product and x["trade_date"] == trade_date and expiry.get(x["contract"], trade_date) >= trade_date]
    if not candidates:
        return {"status": "DEGRADED", "selection_mode": mode, "reason": "no unexpired contract on latest usable date"}
    if mode == "NEAR":
        chosen = min(candidates, key=lambda x: (expiry.get(x["contract"], date.max), x["contract"]))
    else:
        chosen = max(candidates, key=lambda x: (x["open_interest"], x["volume"], -int(x["contract"][2:])))
    series = history.get(chosen["contract"]) or []
    point = next((x for x in series if x["trade_date"] == trade_date), None)
    if not point or point.get("basis_change_5d_pct_points") is None:
        return {"status": "DEGRADED", "selection_mode": mode, "contract": chosen["contract"], "reason": "selected contract lacks 5 prior same-contract observations"}
    delta = point["basis_change_5d_pct_points"]
    return {
        "status": "READY",
        "selection_mode": mode,
        "contract": chosen["contract"],
        "expiry_date": expiry.get(chosen["contract"]).isoformat() if expiry.get(chosen["contract"]) else None,
        "trade_date": trade_date.isoformat(),
        "futures_close": round(point["close"], 4),
        "spot_close": round(point["spot_close"], 4),
        "basis_close_pct": round(point["basis_close_pct"], 4),
        "basis_change_5d_pct_points": round(delta, 4),
        "direction": "STRENGTHENING" if delta > 0 else ("WEAKENING" if delta < 0 else "FLAT"),
        "open_interest": point["open_interest"],
        "volume": point["volume"],
    }


def build(root: Path = ROOT) -> dict:
    conclusion = read_json(root / CONCLUSION.relative_to(ROOT), {})
    base = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "mode": "FORMAL_IF_IC_5D_BASIS_DYNAMIC_EVIDENCE",
        "evidence_id": "if_ic_basis_5d",
        "display_name": "IF／IC 5日基差变化证据",
        "read_only": True,
        "use_as_decision_evidence": True,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "applies_to_etf_codes": ["561980", "588000", "159781", "159992", "515880", "159326"],
        "research_source": "research/backtests/index_futures_if_ic_basis_formal_conclusion.json",
        "point_in_time_rule": "只使用早于decision_market_date的完整CFFEX逐合约与对应现货日收盘；5日变化先在同一真实合约内部计算，再进行NEAR/PIT_MAIN选择，禁止跨换月拼接。",
    }
    if not (conclusion.get("decision_eligible") and conclusion.get("production_context_integration")):
        return {**base, "status": "BLOCKED", "use_in_current_decision": False, "reason": "formal IF/IC conversion contract not authorized"}
    validated = {str(x.get("signal_id")) for x in conclusion.get("validated_signals") or [] if x.get("decision_eligible")}
    if not {"IF_basis_change_5d", "IC_basis_change_5d"}.issubset(validated):
        return {**base, "status": "BLOCKED", "use_in_current_decision": False, "reason": "validated IF/IC signal set incomplete"}

    decision_date = _decision_market_date(root)
    cutoff = decision_date - timedelta(days=1)
    start = cutoff - timedelta(days=95)
    try:
        futures = _fetch_futures(start, cutoff)
        spot = {product: _fetch_spot(code, start, cutoff) for product, code in PRODUCTS.items()}
    except Exception as exc:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "decision_market_date": decision_date.isoformat(), "reason": f"dynamic input fetch failed: {type(exc).__name__}: {exc}"}

    available_dates = sorted({x["trade_date"] for x in futures if x["trade_date"] < decision_date})
    if not available_dates:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "decision_market_date": decision_date.isoformat(), "reason": "no PIT-usable CFFEX date"}
    fact_date = available_dates[-1]
    expiry = _expiry_map(futures, fact_date)
    products = {}
    for product in ("IF", "IC"):
        hist = _contract_basis_history([x for x in futures if x["product"] == product], spot[product])
        near = _select(product, "NEAR", fact_date, futures, expiry, hist)
        main = _select(product, "PIT_MAIN", fact_date, futures, expiry, hist)
        dirs = [x.get("direction") for x in (near, main) if x.get("status") == "READY"]
        consensus = "MIXED_OR_INCOMPLETE"
        if len(dirs) == 2 and all(x == "STRENGTHENING" for x in dirs):
            consensus = "STRENGTHENING"
        elif len(dirs) == 2 and all(x == "WEAKENING" for x in dirs):
            consensus = "WEAKENING"
        elif len(dirs) == 2 and all(x == "FLAT" for x in dirs):
            consensus = "FLAT"
        products[product] = {
            "role": "PRIMARY" if product == "IF" else "SUPPLEMENTARY",
            "status": "READY" if near.get("status") == "READY" and main.get("status") == "READY" else "DEGRADED",
            "near": near,
            "pit_main": main,
            "consensus": consensus,
        }
    ready = all(x.get("status") == "READY" for x in products.values())
    return {
        **base,
        "status": "READY" if ready else "DEGRADED",
        "use_in_current_decision": ready,
        "decision_market_date": decision_date.isoformat(),
        "fact_latest_date": fact_date.isoformat(),
        "provider": {"futures": "CFFEX official monthly history archive", "spot": "eastmoney_push2his CSI300/CSI500"},
        "products": products,
        "interpretation_rule": "IF为主证据、IC为补充证据；正5日基差变化只增强、负值只削弱风险资产支持。NEAR与PIT_MAIN同时保留，若口径分歧则按分歧证据处理，不机械合成阈值或方向。",
        "rejected_or_research_only": ["IM_basis_change_5d", "IC_oi_change_1d"],
    }


if __name__ == "__main__":
    result = build(ROOT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result.get("status"), "decision_market_date": result.get("decision_market_date"), "fact_latest_date": result.get("fact_latest_date"), "IF": ((result.get("products") or {}).get("IF") or {}).get("consensus"), "IC": ((result.get("products") or {}).get("IC") or {}).get("consensus"), "trade_signal": result.get("trade_signal")}, ensure_ascii=False))
