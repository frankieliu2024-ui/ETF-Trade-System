import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_monitor_config_has_three_classes_and_guards():
    config = json.loads((ROOT / "market_monitor_config.json").read_text(encoding="utf-8"))
    assert set(config["classes"]) == {"A", "B", "C"}
    assert config["guardrails"]["no_trade_rules"] is True
    assert config["guardrails"]["no_master_write"] is True

def test_matrix_covers_requested_objects_and_no_trade_language():
    matrix = (ROOT / "market_monitor_data_matrix.md").read_text(encoding="utf-8")
    for item in ["SOX", "NDX", "SPX", "VIX", "N225", "HSTECH", "TWII", "KOSPI", "DXY"]:
        assert item in matrix
    assert "买入" not in matrix
    assert "卖出" not in matrix

def test_existing_external_series_quality_is_explicit():
    source_dir = Path(r"C:\Users\刘晓飞\Documents\Codex\ETF波段交易系统\专项回测\outputs\v2214_pool_monitor_20260823\index_history")
    dxy = next(source_dir.glob("DX-Y.NYB_*.csv"))
    gold = next(source_dir.glob("GC_F_COMEX*.csv"))
    for path in (dxy, gold):
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
        valid = [r for r in rows if r.get("close", "").strip()]
        assert valid[-1]["date"] == "2026-08-21"
        assert all(float(r["close"]) > 0 for r in valid)
