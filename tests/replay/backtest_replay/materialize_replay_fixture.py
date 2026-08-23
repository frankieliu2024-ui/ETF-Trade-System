from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


REPLAY_DATE = "2026-08-21"
ETF_CODES = ["561980", "588000", "159781", "159941", "159561", "513520", "513180", "518880"]
INDEX_CODES = {"000001.SH": "000001", "399006.SZ": "399006"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_etf(source_dir: Path, code: str) -> dict:
    matches = list(source_dir.glob(f"{code}_hithink_*.csv"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one source CSV for {code}, got {len(matches)}")
    path = matches[0]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if row.get("date") == REPLAY_DATE]
    if len(rows) != 1:
        raise RuntimeError(f"expected one {REPLAY_DATE} row for {code}, got {len(rows)}")
    row = rows[0]
    values = {"open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]), "volume": float(row["volume"]), "amount": float(row["amount"])}
    return {"asset_class": "ETF", "symbol": code, "thscode": f"{code}.{'SH' if code in {'561980', '588000', '513520', '513180', '518880'} else 'SZ'}", **values, "source_file": f"historical_normalized/{path.name}", "source_sha256": sha256(path), "source_date": REPLAY_DATE}


def load_index(source_dir: Path, thscode: str, symbol: str) -> dict:
    path = source_dir / f"index_{symbol}_{REPLAY_DATE}.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    items = obj.get("data", {}).get("item") or []
    if len(items) != 1 or obj.get("data", {}).get("thscode") != thscode:
        raise RuntimeError(f"invalid index source for {thscode}")
    item = items[0]
    stamp_date = "2026-08-21"
    values = {"open": item["open_price"], "high": item["high_price"], "low": item["low_price"], "close": item["close_price"], "volume": item["volume"], "amount": item["turnover"]}
    return {"asset_class": "A_SHARE_INDEX", "symbol": symbol, "thscode": thscode, **values, "source_file": str(path), "source_sha256": sha256(path), "source_date": stamp_date}


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    source_dir = Path(__import__("os").environ.get("REPLAY_ETF_SOURCE_DIR", r"C:\Users\刘晓飞\Documents\Codex\ETF波段交易系统\专项回测\outputs\hithink_etf_audit_20260823\normalized"))
    replay_sources = root / "tests" / "replay" / "sources"
    out = replay_sources / "market_fixture_2026-08-21.json"
    rows = [load_etf(source_dir, code) for code in ETF_CODES]
    rows.extend(load_index(replay_sources, thscode, symbol) for thscode, symbol in INDEX_CODES.items())
    payload = {"replay_date": REPLAY_DATE, "source_policy": "historical facts on or before replay_date; no future data", "rows": rows}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "replay_date": REPLAY_DATE, "count": len(rows), "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
