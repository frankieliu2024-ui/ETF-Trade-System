from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CFG = ROOT / "config/research/etf_option_risk_stage1.json"
OUT = ROOT / "research/backtests/etf_option_risk_stage1_validation.json"
STATUS = ROOT / "data/state/etf_option_risk_research_status.json"
BJ = timezone(timedelta(hours=8), name="Asia/Shanghai")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64 x64) Chrome/117.0.0.0 Safari/537.36"
HDR = {"Referer": "https://stock.finance.sina.com.cn/", "User-Agent": UA}


def dump(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def sina_list(param: str) -> list[str]:
    r = requests.get(f"https://hq.sinajs.cn/list={param}", headers=HDR, timeout=15)
    r.raise_for_status()
    r.encoding = "gbk"
    text = r.text
    return text.split('"')[1].split(",") if '"' in text else []


def option_codes(underlying: str, call: bool) -> dict[str, list[str]]:
    cate = {"510050": "50ETF", "510300": "300ETF", "588000": "科创50ETF", "510500": "500ETF"}.get(underlying)
    if not cate:
        raise ValueError(f"unsupported underlying {underlying}")
    url = "https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.getStockName"
    r = requests.get(url, params={"exchange": "null", "cate": cate}, headers=HDR, timeout=15)
    r.raise_for_status()
    months = (((r.json().get("result") or {}).get("data") or {}).get("contractMonth") or [])
    months = [str(m).replace("-", "")[2:] for m in months[1:]]
    prefix = "OP_UP_" if call else "OP_DOWN_"
    out = {}
    for m in months:
        vals = sina_list(f"{prefix}{underlying}{m}")
        codes = [x.replace("CON_OP_", "") for x in vals if x.startswith("CON_OP_")]
        if codes:
            out[m] = codes
    return out


def tquote(code: str) -> dict:
    v = sina_list(f"CON_OP_{code}")
    if len(v) < 43:
        return {}
    return {
        "bid_vol": fnum(v[0]), "bid": fnum(v[1]), "last": fnum(v[2]), "ask": fnum(v[3]), "ask_vol": fnum(v[4]),
        "open_interest": fnum(v[5]), "pct": fnum(v[6]), "strike": fnum(v[7]), "prev_close": fnum(v[8]),
        "open": fnum(v[9]), "name": v[37], "high": fnum(v[39]), "low": fnum(v[40]), "volume": fnum(v[41]), "amount": fnum(v[42]),
    }


def greeks(code: str) -> dict:
    raw = sina_list(f"CON_SO_{code}")
    if len(raw) < 16:
        return {}
    v = [raw[0]] + raw[4:]
    return {
        "name": v[0], "volume": fnum(v[1]), "delta": fnum(v[2]), "gamma": fnum(v[3]), "theta": fnum(v[4]),
        "vega": fnum(v[5]), "iv": fnum(v[6]), "high": fnum(v[7]), "low": fnum(v[8]), "trade_code": v[9],
        "strike": fnum(v[10]), "last": fnum(v[11]), "theory": fnum(v[12]),
    }


def enrich(codes: list[str], side: str) -> list[dict]:
    rows = []
    for code in codes:
        try:
            q, g = tquote(code), greeks(code)
            strike = g.get("strike") if g.get("strike") is not None else q.get("strike")
            if strike is None or g.get("iv") is None or g.get("delta") is None:
                continue
            rows.append({"code": code, "side": side, "strike": strike, "iv": g.get("iv"), "delta": g.get("delta"),
                         "open_interest": q.get("open_interest"), "volume": q.get("volume"), "last": q.get("last"), "name": q.get("name") or g.get("name")})
        except Exception:
            continue
    return rows


def atm_row(rows: list[dict], side: str):
    target = 0.5 if side == "CALL" else -0.5
    valid = [r for r in rows if r.get("delta") is not None]
    return min(valid, key=lambda r: abs(r["delta"] - target)) if valid else None


def paired_atm(call_rows: list[dict], put_rows: list[dict]) -> dict:
    c = atm_row(call_rows, "CALL")
    p = atm_row(put_rows, "PUT")
    if not c or not p:
        return {"status": "INSUFFICIENT"}
    common = sorted(set(r["strike"] for r in call_rows) & set(r["strike"] for r in put_rows))
    if common:
        def score(k):
            cr = min((r for r in call_rows if r["strike"] == k), key=lambda x: abs(x["delta"] - 0.5))
            pr = min((r for r in put_rows if r["strike"] == k), key=lambda x: abs(x["delta"] + 0.5))
            return abs(cr["delta"] - 0.5) + abs(pr["delta"] + 0.5)
        k = min(common, key=score)
        c = min((r for r in call_rows if r["strike"] == k), key=lambda x: abs(x["delta"] - 0.5))
        p = min((r for r in put_rows if r["strike"] == k), key=lambda x: abs(x["delta"] + 0.5))
    mean_iv = (c["iv"] + p["iv"]) / 2.0
    return {"status": "READY", "strike": c["strike"], "call": c, "put": p,
            "atm_mean_iv": round(mean_iv, 6), "put_minus_call_iv": round(p["iv"] - c["iv"], 6)}


def oi_ratio(call_rows: list[dict], put_rows: list[dict]):
    call_oi = sum(r.get("open_interest") or 0 for r in call_rows)
    put_oi = sum(r.get("open_interest") or 0 for r in put_rows)
    return {"call_open_interest": call_oi, "put_open_interest": put_oi,
            "put_call_open_interest_ratio": round(put_oi / call_oi, 6) if call_oi else None}


def previous_month_codes(underlying: str, n: int = 3) -> list[dict]:
    now = datetime.now(BJ)
    y, m = now.year, now.month
    probes = []
    for i in range(1, n + 1):
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        key = f"{yy % 100:02d}{mm:02d}"
        try:
            vals = sina_list(f"OP_UP_{underlying}{key}")
            codes = [x for x in vals if x.startswith("CON_OP_")]
            probes.append({"month": key, "returned_contract_count": len(codes),
                           "expired_contract_metadata_visible": bool(codes),
                           "historical_daily_iv_timeseries_proven": False})
        except Exception as e:
            probes.append({"month": key, "returned_contract_count": 0,
                           "expired_contract_metadata_visible": False,
                           "historical_daily_iv_timeseries_proven": False,
                           "error": str(e)[:180]})
    return probes


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    underlying = cfg["target"]["option_underlying"]
    generated = datetime.now(BJ).isoformat(timespec="seconds")
    errors = []
    try:
        calls = option_codes(underlying, True)
        puts = option_codes(underlying, False)
    except Exception as e:
        calls, puts = {}, {}
        errors.append(f"contract_list:{e}")

    common_months = [m for m in calls if m in puts]
    expiry_results = []
    for month in common_months[:2]:
        cr = enrich(calls[month], "CALL")
        pr = enrich(puts[month], "PUT")
        pair = paired_atm(cr, pr)
        expiry_results.append({"month": month, "call_contracts": len(calls[month]), "put_contracts": len(puts[month]),
                               "usable_call_iv_rows": len(cr), "usable_put_iv_rows": len(pr), "atm": pair,
                               "open_interest": oi_ratio(cr, pr)})

    live_ok = bool(expiry_results and expiry_results[0].get("atm", {}).get("status") == "READY")
    term_ok = len(expiry_results) >= 2 and all(x.get("atm", {}).get("status") == "READY" for x in expiry_results[:2])
    term = round(expiry_results[1]["atm"]["atm_mean_iv"] - expiry_results[0]["atm"]["atm_mean_iv"], 6) if term_ok else None

    historical_probe = previous_month_codes(underlying, 3)
    expired_metadata_visible = any(x.get("expired_contract_metadata_visible") for x in historical_probe)
    historical_status = "EXPIRED_CONTRACT_METADATA_VISIBLE_NO_DAILY_IV_TIMESERIES" if expired_metadata_visible else "NO_HISTORICAL_IV_ARCHIVE_IN_REVIEWED_INTERFACE"

    if not live_ok:
        interpretation, status = "LIVE_CHAIN_FAILED", "FAILED"
    else:
        interpretation, status = "LIVE_FEASIBLE_HISTORY_INSUFFICIENT", "PASS"

    result = {
        "research_id": cfg["research_id"], "generated_at_beijing": generated, "status": status,
        "research_interpretation": interpretation,
        "source": {"project": cfg["source_project"], "provider": "Sina hq.sinajs + StockOptionService",
                   "provider_mode": "current_active_contract_quotes"},
        "target": cfg["target"],
        "live_chain": {"status": "PASS" if live_ok else "FAILED", "active_common_expiries": common_months,
                       "tested_expiries": expiry_results, "near_vs_next_month_atm_iv_spread": term,
                       "term_structure_status": "READY" if term_ok else "INSUFFICIENT"},
        "historical_availability": {"status": historical_status, "expired_month_probes": historical_probe,
                                    "point_in_time_note": "Expired contract codes may remain discoverable, but this is metadata visibility only. The reviewed interface does not establish a daily historical IV/Skew/term-structure time series, so no historical alpha test is permitted."},
        "stage1_gate": {"live_chain_required": True, "live_chain_pass": live_ok, "history_required_for_alpha_claim": True,
                        "history_sufficient_for_alpha_test": False, "alpha_test_performed": False,
                        "reason": "Historical daily PIT IV/Skew/term-structure series is not available through the reviewed interface; expired contract metadata is not a substitute."},
        "decision_eligible": False, "trade_signal": None, "master_override": False, "production_integration": False,
        "recommended_next_step": "Do not integrate into decision_context. Before any alpha claim, locate a genuine historical daily options-risk source; alternatively archive live facts prospectively for future PIT research.",
        "errors": errors,
    }
    dump(OUT, result)
    dump(STATUS, {"research_id": cfg["research_id"], "generated_at_beijing": generated, "status": status,
                  "interpretation": interpretation, "decision_eligible": False, "trade_signal": None,
                  "master_override": False, "production_integration": False, "historical_status": historical_status,
                  "history_sufficient_for_alpha_test": False, "live_chain_status": result["live_chain"]["status"],
                  "further_research_required": bool(live_ok)})
    print(json.dumps({"status": status, "interpretation": interpretation, "live": result["live_chain"],
                      "historical": result["historical_availability"]}, ensure_ascii=False))
    return 0 if live_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
