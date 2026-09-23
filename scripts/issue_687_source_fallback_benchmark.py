from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import formal_etf_opportunity_discovery as d

BJ = timezone(timedelta(hours=8))
OUT = Path("data/state/issue_687_source_fallback_benchmark.json")


def timed(fn, *args):
    start = time.perf_counter()
    value = fn(*args)
    return value, round(time.perf_counter() - start, 3)


def keyset(rows):
    return {(int(x["market_id"]), str(x["code"])) for x in rows}


def sample(rows, keys, limit=50):
    by = {(int(x["market_id"]), str(x["code"])): x for x in rows}
    return [by[k] for k in sorted(keys)[:limit] if k in by]


def main():
    market_date = datetime.now(BJ).date().isoformat()
    east, east_s = timed(d.fetch_broad_etf_spot)
    hithink, hithink_s = timed(d.fetch_hithink_etf_master)

    e, h = keyset(east), keyset(hithink)
    intersection = e & h
    east_only = e - h
    hithink_only = h - e
    feeder_like = [x for x in hithink if any(t in str(x.get("name") or "") for t in ("联接", "连接", "LOF"))]

    # Exercise the actual independent fallback owner directly. This is equivalent
    # to the production branch after Eastmoney has failed, without modifying
    # production state or relying on a natural provider outage.
    fallback_start = time.perf_counter()
    fallback_rows, fallback_meta = d.fetch_official_tencent_broad_spot(market_date)
    fallback_total_s = round(time.perf_counter() - fallback_start, 3)

    official_start = time.perf_counter()
    try:
        official_rows, official_meta = d.fetch_official_etf_master(market_date)
        official_error = None
    except Exception as exc:
        official_rows, official_meta = [], {}
        official_error = f"{type(exc).__name__}:{exc}"
    official_total_s = round(time.perf_counter() - official_start, 3)

    f = keyset(fallback_rows)
    o = keyset(official_rows)
    h_only_live = {
        (int(x["market_id"]), str(x["code"])) for x in fallback_rows
        if (int(x["market_id"]), str(x["code"])) in hithink_only
        and x.get("price") is not None and x.get("prev_close") is not None
    }
    h_only_official = hithink_only & o
    e_only_official = east_only & o
    result = {
        "schema_version": "1.0",
        "issue": 687,
        "generated_at_beijing": datetime.now(BJ).isoformat(),
        "read_only": True,
        "purpose": "bounded GitHub-runner acceptance for ETF universe primary/fallback completeness and latency",
        "counts": {
            "eastmoney": len(e),
            "hithink": len(h),
            "intersection": len(intersection),
            "eastmoney_only": len(east_only),
            "hithink_only": len(hithink_only),
            "fallback_tencent_hydrated": len(f),
            "fallback_missing_vs_hithink": len(h - f),
            "hithink_feeder_or_lof_name_hits": len(feeder_like),
            "official_exchange_master": len(o),
            "hithink_only_with_live_tencent_quote": len(h_only_live),
            "hithink_only_confirmed_by_official_master": len(h_only_official),
            "eastmoney_only_confirmed_by_official_master": len(e_only_official),
        },
        "latency_seconds": {
            "eastmoney_enumeration": east_s,
            "hithink_enumeration_and_parse": hithink_s,
            "fallback_hithink_plus_tencent_total": fallback_total_s,
            "official_exchange_master_diagnostic": official_total_s,
        },
        "fallback_meta": fallback_meta,
        "official_master_meta": official_meta,
        "official_master_error": official_error,
        "samples": {
            "eastmoney_only": sample(east, east_only),
            "hithink_only": sample(hithink, hithink_only),
            "fallback_missing_vs_hithink": sample(hithink, h - f),
            "hithink_feeder_or_lof_name_hits": feeder_like[:50],
            "hithink_only_with_live_tencent_quote": sample(fallback_rows, h_only_live, 120),
            "hithink_only_confirmed_by_official_master": sample(hithink, h_only_official, 120),
            "eastmoney_only_confirmed_by_official_master": sample(east, e_only_official, 20),
        },
        "manual_same_day_reference": {
            "eastmoney_web_grid": 1626,
            "hithink_web_nav_etf_category": 1635,
            "note": "human screenshot benchmark only; equality is not required because page semantics may differ",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
