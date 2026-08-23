from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "专项回测" / "outputs" / "hithink_etf_audit_20260823" / "raw"
OUT = RAW.parent
NORMALIZED = OUT / "normalized"
NORMALIZED.mkdir(parents=True, exist_ok=True)
EXCEL = ROOT / "历史成交EXCEL（截止2026-08-21）"
STAGE1 = ROOT / "专项回测" / "outputs" / "v2214_pool_monitor_20260823" / "etf_history"

CODES = {
    "561980": "SH", "588000": "SH", "159781": "SZ", "159941": "SZ",
    "159687": "SZ", "159561": "SZ", "513520": "SH", "513180": "SH",
    "515880": "SH", "159992": "SZ", "159326": "SZ", "518880": "SH",
    "512400": "SH", "512880": "SH", "159928": "SZ",
}
FORMAL_PREFIX = {
    "561980": "09 ", "588000": "10 ", "159781": "11 ", "159941": "12 ",
    "159687": "13 ", "159561": "14 ", "513520": "15 ", "513180": "16 ",
}


def load_hithink(code: str, suffix: str) -> pd.DataFrame:
    path = RAW / f"{code}_{suffix}.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not obj.get("ok"):
        raise RuntimeError(f"API failure: {code}")
    df = pd.DataFrame(obj["data"]["item"])
    utc = pd.to_datetime(df.pop("date_ms"), unit="ms", utc=True)
    # API stores the Beijing trading date as 16:00 UTC on the prior calendar day.
    df.insert(0, "date", utc.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize())
    df = df.rename(columns={
        "open_price": "open", "high_price": "high", "low_price": "low",
        "close_price": "close", "turnover": "amount",
    })[["date", "open", "high", "low", "close", "volume", "amount"]]
    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def formal_path(prefix: str) -> Path:
    matches = list(EXCEL.glob(f"{prefix}*.xlsx"))
    if len(matches) != 1:
        raise RuntimeError((prefix, matches))
    return matches[0]


def load_formal(code: str) -> pd.DataFrame:
    raw = pd.read_excel(formal_path(FORMAL_PREFIX[code]))
    df = pd.DataFrame({
        "date": pd.to_datetime(raw.iloc[:, 0].astype(str).str.slice(0, 10)),
        "open": pd.to_numeric(raw.iloc[:, 1], errors="coerce"),
        "high": pd.to_numeric(raw.iloc[:, 2], errors="coerce"),
        "low": pd.to_numeric(raw.iloc[:, 3], errors="coerce"),
        "close": pd.to_numeric(raw.iloc[:, 4], errors="coerce"),
        "volume": pd.to_numeric(raw.iloc[:, 7], errors="coerce"),
        "amount": pd.to_numeric(raw.iloc[:, 8], errors="coerce"),
    })
    return df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")


def load_stage1(code: str) -> pd.DataFrame:
    paths = list(STAGE1.glob(f"{code}_*.csv"))
    if len(paths) != 1:
        raise RuntimeError((code, paths))
    df = pd.read_csv(paths[0])
    df["date"] = pd.to_datetime(df["date"])
    return df


def compare(left: pd.DataFrame, right: pd.DataFrame, left_name: str, right_name: str) -> tuple[dict, pd.DataFrame]:
    m = left.merge(right, on="date", how="outer", suffixes=("_left", "_right"), indicator=True)
    both = m[m["_merge"] == "both"].copy()
    rec = {
        "left_source": left_name, "right_source": right_name,
        "left_rows": len(left), "right_rows": len(right), "overlap_rows": len(both),
        "left_only_dates": int((m["_merge"] == "left_only").sum()),
        "right_only_dates": int((m["_merge"] == "right_only").sum()),
    }
    mismatch = pd.DataFrame({"date": both["date"]})
    mismatch_any = np.zeros(len(both), dtype=bool)
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        lc, rc = f"{c}_left", f"{c}_right"
        if lc not in both or rc not in both:
            continue
        valid = both[lc].notna() & both[rc].notna()
        diff = (both[lc] - both[rc]).abs()
        tol = 1e-9 if c in {"open", "high", "low", "close"} else 1e-4
        bad = valid & (diff > tol)
        rec[f"{c}_compared"] = int(valid.sum())
        rec[f"{c}_mismatch"] = int(bad.sum())
        rec[f"{c}_max_abs_diff"] = float(diff[valid].max()) if valid.any() else None
        mismatch_any |= bad.to_numpy()
        mismatch[f"{c}_left"] = both[lc].to_numpy()
        mismatch[f"{c}_right"] = both[rc].to_numpy()
    return rec, mismatch[mismatch_any]


def quality(code: str, df: pd.DataFrame, adjust_value) -> dict:
    prior_close = df["close"].shift(1)
    first_valid_return = (df["close"] / prior_close - 1).dropna()
    return {
        "code": code, "source": "hithink_finance_remote", "rows": len(df),
        "start": df["date"].min().date().isoformat(), "end": df["date"].max().date().isoformat(),
        "duplicate_dates": int(df["date"].duplicated().sum()),
        "missing_ohlc": int(df[["open", "high", "low", "close"]].isna().sum().sum()),
        "missing_volume": int(df["volume"].isna().sum()), "missing_amount": int(df["amount"].isna().sum()),
        "ohlc_logic_errors": int(((df["high"] < df[["open", "close", "low"]].max(axis=1)) |
                                  (df["low"] > df[["open", "close", "high"]].min(axis=1))).sum()),
        "returns_abs_gt_30pct": int((first_valid_return.abs() > .30).sum()),
        "adjust_field": adjust_value,
    }


def main():
    quality_rows, compare_rows, mismatch_rows = [], [], []
    for code, suffix in CODES.items():
        raw_obj = json.loads((RAW / f"{code}_{suffix}.json").read_text(encoding="utf-8"))
        ht = load_hithink(code, suffix)
        ht.to_csv(NORMALIZED / f"{code}_hithink_20210823_20260821.csv", index=False, encoding="utf-8-sig")
        quality_rows.append(quality(code, ht, raw_obj["data"].get("adjust")))
        if code in FORMAL_PREFIX:
            base = load_formal(code)
            # Compare the API-covered window only.
            base = base[base["date"].between(ht["date"].min(), ht["date"].max())]
            rec, bad = compare(ht, base, "hithink", "formal_excel")
        else:
            base = load_stage1(code)
            rec, bad = compare(ht, base, "hithink", "stage1_public")
        rec["code"] = code
        compare_rows.append(rec)
        if not bad.empty:
            bad.insert(0, "code", code)
            mismatch_rows.append(bad)
    q = pd.DataFrame(quality_rows)
    c = pd.DataFrame(compare_rows)
    q.to_csv(OUT / "hithink_data_quality.csv", index=False, encoding="utf-8-sig")
    c.to_csv(OUT / "source_comparison.csv", index=False, encoding="utf-8-sig")
    if mismatch_rows:
        pd.concat(mismatch_rows, ignore_index=True).to_csv(OUT / "mismatch_details.csv", index=False, encoding="utf-8-sig")
    summary = {
        "etfs": len(q), "rows": int(q["rows"].sum()), "last_dates": sorted(q["end"].unique().tolist()),
        "missing_amount": int(q["missing_amount"].sum()), "ohlc_logic_errors": int(q["ohlc_logic_errors"].sum()),
        "price_mismatches": int(sum(c.get(f"{x}_mismatch", pd.Series(dtype=int)).fillna(0).sum() for x in ["open", "high", "low", "close"])),
        "volume_mismatches": int(c.get("volume_mismatch", pd.Series(dtype=int)).fillna(0).sum()),
        "amount_compared": int(c.get("amount_compared", pd.Series(dtype=int)).fillna(0).sum()),
        "amount_mismatches": int(c.get("amount_mismatch", pd.Series(dtype=int)).fillna(0).sum()),
        "adjust_field_values": [None],
        "timezone": "date_ms converted from UTC to Asia/Shanghai before taking trading date",
    }
    (OUT / "audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
