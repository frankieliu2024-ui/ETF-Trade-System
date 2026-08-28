from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import baostock as bs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/state/historical_market_breadth_poc.json"
TEST_DATES = ["2018-06-29", "2020-03-23", "2022-04-25", "2024-02-05", "2026-08-27"]
SAMPLE_SIZE = 8


def fetch_rows(rs):
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock error {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(dict(zip(rs.fields, rs.get_row_data())))
    return rows


def is_a_share(code: str) -> bool:
    return code.startswith(("sh.60", "sh.68", "sz.00", "sz.30"))


def sample_codes(codes, n=SAMPLE_SIZE):
    codes = sorted(set(codes))
    if len(codes) <= n:
        return codes
    idx = {round(i * (len(codes) - 1) / (n - 1)) for i in range(n)}
    return [codes[i] for i in sorted(idx)]


def fetch_daily(code: str, day: str):
    rs = bs.query_history_k_data_plus(
        code,
        "date,code,close,preclose,tradestatus,pctChg",
        start_date=day,
        end_date=day,
        frequency="d",
        adjustflag="3",
    )
    rows = fetch_rows(rs)
    return rows[0] if rows else None


def main():
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed {login.error_code}: {login.error_msg}")
    try:
        basic = fetch_rows(bs.query_stock_basic())
        basic_map = {r.get("code"): r for r in basic if r.get("code")}
        delisted_count = sum(1 for r in basic if r.get("type") == "1" and r.get("status") == "0")
        dates = []
        for day in TEST_DATES:
            universe = fetch_rows(bs.query_all_stock(day=day))
            a_share_rows = [r for r in universe if is_a_share(r.get("code", ""))]
            normal = [r for r in a_share_rows if r.get("tradeStatus") == "1"]
            suspended = [r for r in a_share_rows if r.get("tradeStatus") == "0"]
            chosen = sample_codes([r["code"] for r in normal])
            daily_rows, missing, mismatch = [], [], []
            direction = {"up": 0, "down": 0, "flat": 0}
            for code in chosen:
                row = fetch_daily(code, day)
                if not row:
                    missing.append(code)
                    continue
                daily_rows.append(row)
                if row.get("tradestatus") != "1":
                    mismatch.append(code)
                pct = float(row.get("pctChg") or 0.0)
                direction["up" if pct > 0 else "down" if pct < 0 else "flat"] += 1
            metadata_known = sum(1 for r in a_share_rows if r.get("code") in basic_map)
            dates.append({
                "date": day,
                "all_security_count": len(universe),
                "a_share_count": len(a_share_rows),
                "normal_trade_count": len(normal),
                "suspended_count": len(suspended),
                "metadata_known_ratio": round(metadata_known / len(a_share_rows), 6) if a_share_rows else 0.0,
                "sample_size": len(chosen),
                "sample_daily_rows": len(daily_rows),
                "sample_completeness_ratio": round(len(daily_rows) / len(chosen), 6) if chosen else 0.0,
                "sample_missing_codes": missing,
                "sample_trade_status_mismatch_codes": mismatch,
                "sample_direction_counts": direction,
            })
        gates = {
            "historical_universe_pass": all(d["a_share_count"] > 1000 for d in dates),
            "metadata_pass": all(d["metadata_known_ratio"] >= 0.98 for d in dates),
            "daily_sample_pass": all(d["sample_completeness_ratio"] >= 0.875 for d in dates),
            "trade_status_pass": all(not d["sample_trade_status_mismatch_codes"] for d in dates),
            "delisted_metadata_pass": delisted_count > 0,
        }
        qualified = all(gates.values())
        result = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "HISTORICAL_MARKET_BREADTH_DATA_QUALIFICATION_POC",
            "read_only": True,
            "scope": "沪深A股日频Point-in-Time历史宽度数据资格验证；不生成交易信号，不写正式状态。",
            "provider": "BaoStock",
            "test_dates": TEST_DATES,
            "stock_basic": {"row_count": len(basic), "delisted_stock_metadata_count": delisted_count},
            "dates": dates,
            "gates": gates,
            "status": "QUALIFIED_FOR_LIMITED_FULL_BREADTH_POC" if qualified else "NOT_QUALIFIED",
            "next_step_if_qualified": "只做有限历史区间完整宽度重建和交易所抽样交叉验证；通过后才扩大区间并测试预测力。",
            "decision_boundary": "只验证PIT股票池、历史交易状态和日K可得性；不证明预测力，不进入MASTER或执行层。",
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
