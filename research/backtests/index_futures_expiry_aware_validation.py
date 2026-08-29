#!/usr/bin/env python3
"""Research-only IF/IC/IM expiry-aware panel builder.

This script does not fetch production data, write production state, or emit trade
signals. It transforms point-in-time daily contract data into contract-local
basis/OI features and two non-lookahead selection views: nearest unexpired and
same-day OI main contract.

Required futures CSV columns:
    trade_date, product, contract, expiry_date, close, settlement, volume,
    open_interest
Required spot CSV columns:
    trade_date, product, spot_close

The product key in spot CSV must be IF/IC/IM and map to the corresponding
underlying index used by the research input preparation step.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PRODUCTS = {"IF", "IC", "IM"}
FUTURES_REQUIRED = {
    "trade_date",
    "product",
    "contract",
    "expiry_date",
    "close",
    "settlement",
    "volume",
    "open_interest",
}
SPOT_REQUIRED = {"trade_date", "product", "spot_close"}


def _require_columns(frame: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{name} missing columns: {missing}")


def _numeric(frame: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")


def load_inputs(futures_path: Path, spot_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    futures = pd.read_csv(futures_path)
    spot = pd.read_csv(spot_path)
    _require_columns(futures, FUTURES_REQUIRED, "futures")
    _require_columns(spot, SPOT_REQUIRED, "spot")

    futures["trade_date"] = pd.to_datetime(futures["trade_date"], errors="coerce")
    futures["expiry_date"] = pd.to_datetime(futures["expiry_date"], errors="coerce")
    spot["trade_date"] = pd.to_datetime(spot["trade_date"], errors="coerce")
    _numeric(futures, ["close", "settlement", "volume", "open_interest"])
    _numeric(spot, ["spot_close"])

    futures["product"] = futures["product"].astype(str).str.upper().str.strip()
    futures["contract"] = futures["contract"].astype(str).str.upper().str.strip()
    spot["product"] = spot["product"].astype(str).str.upper().str.strip()

    bad_products = sorted(set(futures["product"].dropna()) - PRODUCTS)
    if bad_products:
        raise ValueError(f"unsupported futures products: {bad_products}")
    bad_spot_products = sorted(set(spot["product"].dropna()) - PRODUCTS)
    if bad_spot_products:
        raise ValueError(f"unsupported spot products: {bad_spot_products}")

    if futures[list(FUTURES_REQUIRED)].isna().any().any():
        nulls = futures[list(FUTURES_REQUIRED)].isna().sum()
        raise ValueError(f"futures contains null required values: {nulls[nulls > 0].to_dict()}")
    if spot[list(SPOT_REQUIRED)].isna().any().any():
        nulls = spot[list(SPOT_REQUIRED)].isna().sum()
        raise ValueError(f"spot contains null required values: {nulls[nulls > 0].to_dict()}")

    dup_futures = futures.duplicated(["trade_date", "product", "contract"], keep=False)
    if dup_futures.any():
        sample = futures.loc[dup_futures, ["trade_date", "product", "contract"]].head(10)
        raise ValueError(f"duplicate futures keys detected: {sample.to_dict('records')}")
    dup_spot = spot.duplicated(["trade_date", "product"], keep=False)
    if dup_spot.any():
        sample = spot.loc[dup_spot, ["trade_date", "product"]].head(10)
        raise ValueError(f"duplicate spot keys detected: {sample.to_dict('records')}")

    futures = futures.sort_values(["product", "contract", "trade_date"]).reset_index(drop=True)
    spot = spot.sort_values(["product", "trade_date"]).reset_index(drop=True)
    return futures, spot


def build_contract_panel(futures: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    panel = futures.merge(spot, on=["trade_date", "product"], how="left", validate="many_to_one")
    if panel["spot_close"].isna().any():
        missing = panel.loc[panel["spot_close"].isna(), ["trade_date", "product"]].drop_duplicates().head(20)
        raise ValueError(f"missing spot close for futures rows: {missing.to_dict('records')}")

    panel["days_to_expiry"] = (panel["expiry_date"] - panel["trade_date"]).dt.days
    if (panel["days_to_expiry"] < 0).any():
        sample = panel.loc[panel["days_to_expiry"] < 0, ["trade_date", "contract", "expiry_date"]].head(20)
        raise ValueError(f"rows after expiry detected: {sample.to_dict('records')}")

    panel["basis_close_pct"] = panel["close"] / panel["spot_close"] - 1.0
    panel["basis_settlement_pct"] = panel["settlement"] / panel["spot_close"] - 1.0

    # Critical anti-roll rule: changes are calculated inside each actual contract
    # before selecting near/main views. A contract switch therefore cannot create
    # a synthetic basis/OI jump.
    grouped = panel.groupby(["product", "contract"], sort=False)
    for field in ["basis_close_pct", "basis_settlement_pct", "open_interest"]:
        panel[f"{field}_change_1d"] = grouped[field].diff(1)
        panel[f"{field}_change_5d"] = grouped[field].diff(5)

    return panel.sort_values(["trade_date", "product", "expiry_date", "contract"]).reset_index(drop=True)


def _mark_selection(panel: pd.DataFrame, mode: str) -> pd.DataFrame:
    if mode == "near":
        ordered = panel.sort_values(
            ["trade_date", "product", "expiry_date", "contract"],
            ascending=[True, True, True, True],
        )
    elif mode == "main":
        # Same-day PIT only: highest open interest; volume is a deterministic
        # same-day tie-breaker. No T+1 information is used.
        ordered = panel.sort_values(
            ["trade_date", "product", "open_interest", "volume", "expiry_date", "contract"],
            ascending=[True, True, False, False, True, True],
        )
    else:
        raise ValueError(f"unknown selection mode: {mode}")

    selected = ordered.groupby(["trade_date", "product"], as_index=False, sort=True).head(1).copy()
    selected = selected.sort_values(["product", "trade_date"]).reset_index(drop=True)
    selected["selection_mode"] = mode.upper()
    selected["previous_selected_contract"] = selected.groupby("product")["contract"].shift(1)
    selected["roll_flag"] = (
        selected["previous_selected_contract"].notna()
        & (selected["contract"] != selected["previous_selected_contract"])
    )
    return selected


def build_qa(panel: pd.DataFrame, near: pd.DataFrame, main: pd.DataFrame) -> dict:
    def summary(frame: pd.DataFrame) -> dict:
        return {
            "rows": int(len(frame)),
            "start_date": frame["trade_date"].min().date().isoformat() if len(frame) else None,
            "end_date": frame["trade_date"].max().date().isoformat() if len(frame) else None,
            "roll_count": int(frame["roll_flag"].sum()) if "roll_flag" in frame else None,
            "product_rows": {k: int(v) for k, v in frame.groupby("product").size().to_dict().items()},
        }

    availability = {}
    for product, frame in panel.groupby("product"):
        availability[product] = {
            "contracts": int(frame["contract"].nunique()),
            "trade_days": int(frame["trade_date"].nunique()),
            "close_basis_change_5d_non_null": int(frame["basis_close_pct_change_5d"].notna().sum()),
            "oi_change_1d_non_null": int(frame["open_interest_change_1d"].notna().sum()),
            "min_days_to_expiry": int(frame["days_to_expiry"].min()),
            "max_days_to_expiry": int(frame["days_to_expiry"].max()),
        }

    return {
        "mode": "RESEARCH_ONLY_INDEX_FUTURES_EXPIRY_AWARE_PANEL",
        "trade_signal": None,
        "production_context_integration": False,
        "contract_local_change_rule": True,
        "panel": summary(panel),
        "near": summary(near),
        "main": summary(main),
        "availability": availability,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--futures", required=True, type=Path)
    parser.add_argument("--spot", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    futures, spot = load_inputs(args.futures, args.spot)
    panel = build_contract_panel(futures, spot)
    near = _mark_selection(panel, "near")
    main_contract = _mark_selection(panel, "main")
    qa = build_qa(panel, near, main_contract)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.output_dir / "index_futures_expiry_aware_contract_panel.csv", index=False)
    near.to_csv(args.output_dir / "index_futures_expiry_aware_near.csv", index=False)
    main_contract.to_csv(args.output_dir / "index_futures_expiry_aware_main.csv", index=False)
    (args.output_dir / "index_futures_expiry_aware_qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
