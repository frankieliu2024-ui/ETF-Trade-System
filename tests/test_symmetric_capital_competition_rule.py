from pathlib import Path


def test_master_forbids_default_hold_and_default_buy():
    text = (Path(__file__).resolve().parents[1] / "ETF规则_MASTER.md").read_text(encoding="utf-8")
    assert "持仓不享有既有状态豁免" in text
    assert "新候选也不享有默认买入权" in text
    assert "不得以“已在持仓中”自动选择HOLD" in text
    assert "不得以“发现为候选”自动选择BUY" in text
    assert "迁移成本" in text
