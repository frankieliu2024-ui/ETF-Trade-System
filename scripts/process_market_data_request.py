from __future__ import annotations

import argparse, csv, json, os, shutil, subprocess, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://fuyao.aicubes.cn"
SHANGHAI = ZoneInfo("Asia/Shanghai")
RESULT_DIR = ROOT / "data/market/on_demand/results"
DATASET_DIR = ROOT / "data/market/on_demand/datasets"


def to_thscode(code: str) -> str:
    v = str(code).strip().upper()
    if "." in v: return v
    if v.startswith(("60", "68", "51", "56", "58")): return f"{v}.SH"
    if v.startswith(("00", "30", "15")): return f"{v}.SZ"
    if v.startswith(("4", "8", "92")): return f"{v}.BJ"
    raise ValueError(f"cannot infer exchange for {code}")


def api_request(path: str, params: dict) -> dict:
    key = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
    if not key: raise RuntimeError("HITHINK_FINANCE_API_KEY is not configured")
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-api-key": key, "Accept": "application/json", "User-Agent": "ETF-Trade-System/on-demand-v1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"hithink code={payload.get('code')}: {payload.get('message')}; request_id={payload.get('request_id')}")
    return payload


def ms(day: date, end=False) -> int:
    dt = datetime(day.year, day.month, day.day, tzinfo=SHANGHAI)
    if end: dt += timedelta(days=1) - timedelta(milliseconds=1)
    return int(dt.timestamp() * 1000)


def normalize_rows(items: list[dict]) -> list[dict]:
    rows = []
    for x in items:
        d = datetime.fromtimestamp(int(x["date_ms"]) / 1000, tz=SHANGHAI).date().isoformat()
        rows.append({"date": d, "open": x.get("open_price"), "high": x.get("high_price"), "low": x.get("low_price"), "close": x.get("close_price"), "volume": x.get("volume"), "amount": x.get("turnover")})
    dedup = {r["date"]: r for r in rows}
    return [dedup[k] for k in sorted(dedup)]


def validate_rows(rows: list[dict]) -> None:
    if not rows: raise RuntimeError("history returned no rows")
    for r in rows:
        vals = [r.get(k) for k in ("open", "high", "low", "close")]
        if any(v is None for v in vals): raise RuntimeError(f"missing OHLC on {r['date']}")
        o, h, l, c = map(float, vals)
        if h < max(o, l, c) or l > min(o, h, c): raise RuntimeError(f"invalid OHLC relation on {r['date']}")


def stock_history(thscode: str, start: date, end: date, adjust: str):
    rows, ids, cursor = [], [], start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=364), end)
        payload = api_request("/api/a-share/prices/historical", {"thscode": thscode, "interval": "1d", "start": ms(cursor), "end": ms(window_end, True), "adjust": adjust})
        rows.extend(normalize_rows(payload.get("data", {}).get("item") or []))
        if payload.get("request_id"): ids.append(str(payload["request_id"]))
        cursor = window_end + timedelta(days=1)
    dedup = {r["date"]: r for r in rows}
    return [dedup[k] for k in sorted(dedup)], ids


def etf_history(thscode: str, start: date, end: date):
    cli = shutil.which("hithink-finance")
    if not cli: raise RuntimeError("hithink-finance CLI not found")
    rows, ids, cursor = [], [], start
    tmp = ROOT / "data/market/on_demand/tmp"; tmp.mkdir(parents=True, exist_ok=True)
    while cursor <= end:
        try: candidate = cursor.replace(year=cursor.year + 4)
        except ValueError: candidate = cursor.replace(year=cursor.year + 4, day=28)
        window_end = min(candidate, end)
        raw = tmp / f"{thscode.replace('.', '_')}_{cursor}_{window_end}.json"
        cp = subprocess.run([cli, "fund", "history", "--thscode", thscode, "--start-ms", str(ms(cursor)), "--end-ms", str(ms(window_end, True)), "--output", str(raw), "--format", "json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
        if cp.returncode != 0 or not raw.exists(): raise RuntimeError(f"ETF history CLI failed: {(cp.stderr or cp.stdout)[-800:]}")
        obj = json.loads(raw.read_text(encoding="utf-8"))
        if not obj.get("ok") or obj.get("meta", {}).get("source") != "remote": raise RuntimeError(f"ETF history non-remote/failed: {obj.get('meta')}")
        rows.extend(normalize_rows(obj.get("data", {}).get("item") or []))
        if obj.get("request_id"): ids.append(str(obj["request_id"]))
        cursor = window_end + timedelta(days=1)
    dedup = {r["date"]: r for r in rows}
    return [dedup[k] for k in sorted(dedup)], ids


def snapshot(asset_type: str, thscode: str) -> dict:
    if asset_type == "stock": payload = api_request("/api/a-share/prices/snapshot", {"thscodes": thscode})
    elif asset_type == "etf": payload = api_request("/api/fund/market/snapshot", {"thscode": thscode})
    else: raise ValueError("asset_type must be stock or etf")
    items = payload.get("data", {}).get("item") or []
    item = next((x for x in items if x.get("thscode") == thscode), None)
    if item is None: raise RuntimeError(f"snapshot returned no exact item for {thscode}")
    return {"open": item.get("open_price"), "high": item.get("high_price"), "low": item.get("low_price"), "last": item.get("last_price"), "prev_close": item.get("prev_price"), "change_pct": item.get("price_change_ratio_pct"), "volume": item.get("volume"), "amount": item.get("turnover"), "provider_timestamp_ms": payload.get("data", {}).get("timestamp"), "provider_request_id": payload.get("request_id")}


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume", "amount"]); w.writeheader(); w.writerows(rows)


def process(path: Path) -> dict:
    req = json.loads(path.read_text(encoding="utf-8")); request_id = req.get("request_id") or path.stem
    asset_type = str(req.get("asset_type", "stock")).lower(); mode = str(req.get("mode", "snapshot")).lower()
    result = {"ok": False, "request_id": request_id, "provider": "hithink-finance", "asset_type": asset_type, "mode": mode, "processed_at": datetime.now(SHANGHAI).isoformat(), "request_file": str(path.relative_to(ROOT))}
    try:
        if not req.get("code"):
            raise ValueError("request has no code; legacy full-snapshot trigger files are not single-object requests")
        thscode = to_thscode(req["code"])
        result["thscode"] = thscode
        if mode == "snapshot": result.update(snapshot(asset_type, thscode)); result["ok"] = True
        elif mode == "history":
            start = date.fromisoformat(req["start_date"]); end = date.fromisoformat(req["end_date"])
            if end < start: raise ValueError("end_date before start_date")
            adjust = str(req.get("adjust", "none"))
            rows, ids = stock_history(thscode, start, end, adjust) if asset_type == "stock" else etf_history(thscode, start, end)
            validate_rows(rows); dataset = DATASET_DIR / f"{request_id}.csv"; write_csv(dataset, rows)
            result.update({"ok": True, "start_date": start.isoformat(), "end_date": end.isoformat(), "rows": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"], "adjust": adjust if asset_type == "stock" else None, "dataset": str(dataset.relative_to(ROOT)), "provider_request_ids": ids, "last_bar": rows[-1]})
        else: raise ValueError("mode must be snapshot or history")
    except Exception as exc: result["error"] = f"{type(exc).__name__}: {exc}"[-2000:]
    RESULT_DIR.mkdir(parents=True, exist_ok=True); out = RESULT_DIR / f"{request_id}.json"; out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    p = argparse.ArgumentParser(); p.add_argument("request_file"); args = p.parse_args(); result = process((ROOT / args.request_file).resolve()); print(json.dumps(result, ensure_ascii=False)); raise SystemExit(0 if result.get("ok") else 2)

if __name__ == "__main__": main()
