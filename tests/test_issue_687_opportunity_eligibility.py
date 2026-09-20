from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("discovery", ROOT / "scripts/formal_etf_opportunity_discovery.py")
discovery = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(discovery)


def row(code: str, name: str, *, r60: float = 3.0, change: float = 1.0, amplitude: float = 1.2):
    return {
        "code": code, "name": name, "market_id": 1, "price": 1.0,
        "change_pct": change, "amount": 50_000_000.0, "amplitude_pct": amplitude,
        "turnover_pct": 2.0, "volume_ratio": 1.1, "high": 1.01, "low": 0.99,
        "open": 1.0, "prev_close": 0.99, "return_60d_pct": r60,
        "return_ytd_pct": 5.0, "listing_date": "20250101", "provider_timestamp": 1,
    }


def test_cash_management_etf_does_not_consume_scarce_history_queue() -> None:
    rows = [
        row("511620", "货币ETF国泰", r60=0.01, change=0.01, amplitude=0.01),
        row("510230", "金融ETF国泰"),
    ]
    selected = discovery._bounded_prefilter(rows, set(), "2026-09-18")
    assert "511620" not in {x["code"] for x in selected}
    assert "510230" in {x["code"] for x in selected}


def test_fixed_income_is_not_blanket_excluded() -> None:
    rows = [
        row("511090", "30年国债ETF鹏扬", r60=4.0, change=0.8, amplitude=1.1),
        row("511180", "可转债ETF海富通", r60=6.0, change=1.2, amplitude=1.8),
    ]
    selected = discovery._bounded_prefilter(rows, set(), "2026-09-18")
    assert {x["code"] for x in selected} == {"511090", "511180"}


def test_gate_is_not_a_trade_or_observation_authority() -> None:
    candidate = row("510230", "金融ETF国泰")
    assert discovery._opportunity_eligible(candidate) is True
    assert "trial_confirm_permission" not in candidate
    assert "trade_signal" not in candidate
    assert "management_identity" not in candidate
