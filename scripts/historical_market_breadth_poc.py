from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import baostock as bs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/state/historical_market_breadth_poc.json"
TEST_DATES = ["2018-06-29", "2020-03-23", "2022-04-25", "2024-02-05", "2026-08-27"]


def fetch_rows(rs):
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock error {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(dict(zip(rs.fields, rs.get_row_data())))
    return rows


def is_a_share(code: str) -> bool:
    return code.startswith(("sh.60", "sh.68", "sz.00", "sz.30"))


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
        dates = []
        for day in TEST_DATES:
            universe = fetch_rows(bs.query_all_stock(day=day))
            a_share_rows = [r for r in universe if is_a_share(r.get("code", ""))]
            normal = [r for r in a_share_rows if r.get("tradeStatus") == "1"]
            suspended = [r for r in a_share_rows if r.get("tradeStatus") == "0"]
            sample_code = normal[len(normal) // 2]["code"] if normal else None
            sample_daily = fetch_daily(sample_code, day) if sample_code else None
            dates.append({
                "date": day,
                "all_security_count": len(universe),
                "a_share_count": len(a_share_rows),
                "normal_trade_count": len(normal),
                "suspended_count": len(suspended),
                "sample_code": sample_code,
                "sample_daily_available": bool(sample_daily),
                "sample_daily": sample_daily,
            })

        gates = {
            "historical_universe_pass": all(d["a_share_count"] > 1000 for d in dates),
            "historical_universe_changes_over_time": len({d["a_share_count"] for d in dates}) > 1,
            "trade_status_available": all(d["normal_trade_count"] > 0 for d in dates),
            "daily_history_sample_pass": all(d["sample_daily_available"] for d in dates),
        }
        qualified = all(gates.values())
        result = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "HISTORICAL_MARKET_BREADTH_DATA_QUALIFICATION_POC",
            "read_only": True,
            "scope": "沪深A股日频Point-in-Time历史宽度第一阶段数据资格验证；不生成交易信号，不写正式状态。",
            "provider": "BaoStock",
            "test_dates": TEST_DATES,
            "dates": dates,
            "gates": gates,
            "status": "QUALIFIED_FOR_LIMITED_FULL_BREADTH_POC" if qualified else "NOT_QUALIFIED",
            "next_step_if_qualified": "只做短历史区间的完整上涨/下跌/平盘重建，并与交易所历史汇总做抽样交叉验证；通过后才扩大区间。",
            "decision_boundary": "本阶段只证明指定历史日期PIT股票池、交易状态和对应日K可访问；不证明全市场逐证券回补吞吐，也不证明宽度预测力。",
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
